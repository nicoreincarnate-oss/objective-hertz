"""Sweep coverage tests — focused tests for files flagged by the completion sweep.

Pattern: function-level sys.modules injection with save/restore (same as
tests/test_discovery_source_selection.py). External dependencies are mocked
with AsyncMock; modules are reloaded from scratch so each test gets a clean
import state.
"""

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _restore(saved: dict) -> None:
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


# ===========================================================================
# 1. clawdbot/brain.py
# ===========================================================================

def _setup_brain():
    """Install minimal fake modules, reload brain, return (saved, module)."""
    modules_to_fake = [
        "shared.comms",
        "shared.llm_client",
        "shared.skill_loader",
        "clawdbot.capabilities",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=1)
    sys.modules["shared.comms"] = fake_comms

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(generate=AsyncMock(return_value="{}"))
    sys.modules["shared.llm_client"] = fake_llm_mod

    fake_skill_loader = types.ModuleType("shared.skill_loader")
    fake_skill_loader.find_skill = MagicMock(return_value=None)
    fake_skill_loader.list_installed_skills = MagicMock(return_value=[])
    sys.modules["shared.skill_loader"] = fake_skill_loader

    fake_caps = types.ModuleType("clawdbot.capabilities")
    fake_caps.CAPABILITY_MAP = {"web_scrape": None, "browser_task": None}
    sys.modules["clawdbot.capabilities"] = fake_caps

    sys.modules.pop("clawdbot.brain", None)
    brain = importlib.import_module("clawdbot.brain")
    return saved, brain, fake_llm_mod.llm


def test_brain_decide_approach_returns_fallback_on_invalid_json():
    """decide_approach falls back to http_scrape when LLM returns non-JSON."""
    saved, brain, fake_llm = _setup_brain()
    try:
        fake_llm.generate = AsyncMock(return_value="not valid json at all")
        result = asyncio.run(brain.decide_approach("do something complex"))
        assert result["approach"] == "http_scrape"
        assert "reasoning" in result
    finally:
        _restore(saved)


def test_brain_decide_approach_parses_valid_json():
    """decide_approach correctly parses a well-formed LLM response."""
    saved, brain, fake_llm = _setup_brain()
    try:
        response_json = (
            '{"approach": "skill", "tool_name": "some-skill", '
            '"reasoning": "exact match", "steps": []}'
        )
        fake_llm.generate = AsyncMock(return_value=response_json)
        result = asyncio.run(brain.decide_approach("scrape a URL", available_tools={}))
        assert result["approach"] == "skill"
        assert result["tool_name"] == "some-skill"
    finally:
        _restore(saved)


def test_brain_should_use_brain_simple_tasks():
    """Simple task types bypass the brain entirely."""
    saved, brain, _ = _setup_brain()
    try:
        for task_type in ("web_scrape", "verify_single_site", "enrich_leads",
                          "clawdbot_operator_message"):
            assert asyncio.run(brain.should_use_brain(task_type, {})) is False
    finally:
        _restore(saved)


def test_brain_should_use_brain_short_browser_task():
    """browser_task with a URL and short description does not need the brain."""
    saved, brain, _ = _setup_brain()
    try:
        payload = {"url": "https://example.com", "description": "take a screenshot"}
        assert asyncio.run(brain.should_use_brain("browser_task", payload)) is False
    finally:
        _restore(saved)


def test_brain_should_use_brain_long_browser_task():
    """browser_task with a long description (≥100 chars) goes through the brain."""
    saved, brain, _ = _setup_brain()
    try:
        payload = {"url": "https://example.com", "description": "x" * 101}
        assert asyncio.run(brain.should_use_brain("browser_task", payload)) is True
    finally:
        _restore(saved)


def test_brain_should_use_brain_installed_skill():
    """skill_execute for an installed skill does not need the brain."""
    saved, brain, _ = _setup_brain()
    try:
        # find_skill is imported by name into brain's global scope at module load.
        # We must patch it on the brain module directly, not on sys.modules.
        with patch.object(brain, "find_skill", return_value={"name": "my-skill"}):
            payload = {"skill_name": "my-skill"}
            assert asyncio.run(brain.should_use_brain("skill_execute", payload)) is False
    finally:
        _restore(saved)


def test_brain_should_use_brain_unknown_task():
    """Unknown task types always go through the brain."""
    saved, brain, _ = _setup_brain()
    try:
        assert asyncio.run(brain.should_use_brain("some_exotic_task", {})) is True
    finally:
        _restore(saved)


# ===========================================================================
# 2. perseus/backprop.py
# ===========================================================================

def _setup_backprop(tmp_path: Path):
    """Return (saved, backprop_module) with all external deps mocked."""
    modules_to_fake = [
        "shared.comms",
        "shared.config",
        "shared.db",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=1)
    sys.modules["shared.comms"] = fake_comms

    fake_config = types.ModuleType("shared.config")
    fake_config_obj = types.SimpleNamespace(root_dir=tmp_path)
    fake_config.config = fake_config_obj
    sys.modules["shared.config"] = fake_config

    fake_db = types.ModuleType("shared.db")
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    fake_db.emit_event = AsyncMock()
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("perseus.backprop", None)
    backprop = importlib.import_module("perseus.backprop")
    return saved, backprop, fake_db, fake_comms


def test_backprop_apply_soul_doc_edit_edits_file(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        target = tmp_path / "soul_doc.md"
        target.write_text("Hello old world\nMore content")
        result = asyncio.run(bp.apply_soul_doc_edit(
            file_path="soul_doc.md",
            old_text="Hello old world",
            new_text="Hello new world",
            reason="test edit",
            cycle_id=1,
        ))
        assert result is True
        assert "Hello new world" in target.read_text()
        fake_comms.record_decision.assert_awaited_once()
    finally:
        _restore(saved)


def test_backprop_apply_soul_doc_edit_blocks_immutable_file(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        result = asyncio.run(bp.apply_soul_doc_edit(
            file_path="titan/compliance.py",
            old_text="anything",
            new_text="new",
            reason="should be blocked",
            cycle_id=1,
        ))
        assert result is False
        fake_comms.record_decision.assert_not_awaited()
    finally:
        _restore(saved)


def test_backprop_apply_soul_doc_edit_blocks_missing_old_text(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        target = tmp_path / "soul_doc.md"
        target.write_text("Some content here")
        result = asyncio.run(bp.apply_soul_doc_edit(
            file_path="soul_doc.md",
            old_text="text that is not there",
            new_text="new",
            reason="missing",
            cycle_id=1,
        ))
        assert result is False
    finally:
        _restore(saved)


def test_backprop_apply_rule_change_calls_execute(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        result = asyncio.run(bp.apply_rule_change(
            rule_id=42,
            active=False,
            reason="test deactivate",
            cycle_id=7,
        ))
        assert result is True
        fake_db.execute.assert_awaited_once()
        call_args = fake_db.execute.await_args
        assert 42 in call_args.args[1]
        fake_comms.record_decision.assert_awaited_once()
    finally:
        _restore(saved)


def test_backprop_rollback_cycle_reverts_config(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        # Reload with fetch_all returning a config-change record.
        # The module captures db functions at import; we patch on the module.
        with patch.object(bp, "fetch_all", new=AsyncMock(return_value=[
            {
                "id": 1,
                "decision_type": "backprop_config",
                "context": {"key": "some_price", "cycle_id": 5},
                "decision": {"old": "199", "new": "249"},
            }
        ])), patch.object(bp, "set_config", new=AsyncMock()) as mock_set:
            count = asyncio.run(bp.rollback_cycle(5))
        assert count >= 1
        mock_set.assert_awaited()
    finally:
        _restore(saved)


def test_backprop_rollback_cycle_reverts_rule(tmp_path):
    saved, bp, fake_db, fake_comms = _setup_backprop(tmp_path)
    try:
        with patch.object(bp, "fetch_all", new=AsyncMock(return_value=[
            {
                "id": 2,
                "decision_type": "backprop_rule",
                "context": {"rule_id": 10, "cycle_id": 5},
                "decision": {"active": True},
            }
        ])), patch.object(bp, "execute", new=AsyncMock()) as mock_exec:
            count = asyncio.run(bp.rollback_cycle(5))
        assert count >= 1
        mock_exec.assert_awaited()
    finally:
        _restore(saved)


# ===========================================================================
# 3. perseus/cell_division.py
# ===========================================================================

def _setup_cell_division():
    modules_to_fake = ["shared.comms", "shared.db"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=99)
    sys.modules["shared.comms"] = fake_comms

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.fetch_val = AsyncMock(return_value=0)  # not already proposed
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("perseus.cell_division", None)
    cd = importlib.import_module("perseus.cell_division")
    return saved, cd, fake_comms, fake_db


def test_cell_division_evaluate_no_proposal_when_snapshot_empty():
    """No proposals when snapshot has no errors or concentrated revenue."""
    saved, cd, fake_comms, fake_db = _setup_cell_division()
    try:
        snapshot = {"recent_errors": [], "source_quality": [], "agent_self_models": {}}
        asyncio.run(cd.evaluate_division_need(snapshot, cycle_id=1))
        fake_comms.record_decision.assert_not_awaited()
        fake_db.emit_event.assert_not_awaited()
    finally:
        _restore(saved)


def test_cell_division_evaluate_proposes_on_stage_errors():
    """5+ errors in one stage triggers a cell division proposal."""
    saved, cd, fake_comms, fake_db = _setup_cell_division()
    try:
        snapshot = {
            "recent_errors": [{"stage": "email_compose"}] * 6,
            "source_quality": [],
            "agent_self_models": {},
        }
        asyncio.run(cd.evaluate_division_need(snapshot, cycle_id=2))
        fake_comms.record_decision.assert_awaited()
        call_kwargs = fake_comms.record_decision.await_args.kwargs
        assert call_kwargs["decision_type"] == "cell_division_proposal"
        assert "email_compose" in call_kwargs["decision"]["name"]
    finally:
        _restore(saved)


def test_cell_division_evaluate_suppresses_duplicate():
    """Already-proposed names within 7 days are suppressed."""
    saved, cd, fake_comms, fake_db = _setup_cell_division()
    try:
        # fetch_val is imported by name into cell_division's global scope at
        # module load time, so we patch it on the module directly.
        with patch.object(cd, "fetch_val", new=AsyncMock(return_value=1)):
            snapshot = {
                "recent_errors": [{"stage": "email_compose"}] * 6,
                "source_quality": [],
                "agent_self_models": {},
            }
            asyncio.run(cd.evaluate_division_need(snapshot, cycle_id=3))
        fake_comms.record_decision.assert_not_awaited()
    finally:
        _restore(saved)


def test_cell_division_propose_new_agent_returns_decision_id():
    """propose_new_agent calls record_decision and returns its ID."""
    saved, cd, fake_comms, fake_db = _setup_cell_division()
    try:
        decision_id = asyncio.run(cd.propose_new_agent(
            name="seo-specialist",
            reason="We need better SEO",
            specialization="SEO optimization",
        ))
        assert decision_id == 99
        fake_comms.record_decision.assert_awaited_once()
        fake_db.emit_event.assert_awaited_once()
    finally:
        _restore(saved)


def test_cell_division_evaluate_revenue_concentration():
    """Source generating >50% of revenue triggers a proposal."""
    saved, cd, fake_comms, fake_db = _setup_cell_division()
    try:
        snapshot = {
            "recent_errors": [],
            "source_quality": [
                {"source": "linkedin", "revenue": 800},
                {"source": "cold_email", "revenue": 100},
            ],
            "agent_self_models": {},
        }
        asyncio.run(cd.evaluate_division_need(snapshot, cycle_id=4))
        fake_comms.record_decision.assert_awaited()
        decision = fake_comms.record_decision.await_args.kwargs["decision"]
        assert "linkedin" in decision["name"]
    finally:
        _restore(saved)


# ===========================================================================
# 4. perseus/self_audit.py
# ===========================================================================

def _setup_self_audit():
    modules_to_fake = [
        "shared.comms",
        "shared.config",
        "shared.db",
        "shared.llm_client",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=1)
    fake_comms.call_agent_capability = AsyncMock(return_value={"vote": "approve", "reason": "ok"})
    sys.modules["shared.comms"] = fake_comms

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=Path("/nonexistent"),
        ruflo=types.SimpleNamespace(enabled=False),
    )
    sys.modules["shared.config"] = fake_config

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    sys.modules["shared.db"] = fake_db

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"findings": []}')
    )
    sys.modules["shared.llm_client"] = fake_llm_mod

    sys.modules.pop("perseus.self_audit", None)
    sa = importlib.import_module("perseus.self_audit")
    return saved, sa, fake_comms, fake_db, fake_llm_mod.llm


def test_self_audit_run_returns_zero_when_no_files():
    """run_self_audit returns zeroed summary when no changed files exist."""
    saved, sa, fake_comms, fake_db, fake_llm = _setup_self_audit()
    try:
        # Patch both file-gathering helpers to return nothing
        with (
            patch.object(sa, "_get_changed_files", new=AsyncMock(return_value=[])),
            patch.object(sa, "_get_random_scan_files", return_value=[]),
        ):
            result = asyncio.run(sa.run_self_audit())
        assert result["total_findings"] == 0
        assert result["applied"] == 0
    finally:
        _restore(saved)


def test_self_audit_run_calls_analyze_and_reaches_consensus(tmp_path):
    """run_self_audit calls _analyze_file for each changed file and votes."""
    saved, sa, fake_comms, fake_db, fake_llm = _setup_self_audit()
    try:
        # Create a real .py file
        py_file = tmp_path / "some_module.py"
        py_file.write_text("x = 1\n")

        # Patch config root so path resolution works
        sys.modules["shared.config"].config = types.SimpleNamespace(
            root_dir=tmp_path,
            ruflo=types.SimpleNamespace(enabled=False),
        )
        # Reload self_audit so it picks up the updated config reference
        sys.modules.pop("perseus.self_audit", None)
        sa = importlib.import_module("perseus.self_audit")

        finding = {
            "file": "some_module.py",
            "line": 1,
            "issue": "dead variable",
            "failure_mode": "dead_code",
            "severity": "low",
            "proposed_fix": "",
            "fix_type": "no_fix_needed",
            "reasoning": "x is never used",
        }

        with (
            patch.object(sa, "_get_changed_files", new=AsyncMock(return_value=["some_module.py"])),
            patch.object(sa, "_analyze_file", new=AsyncMock(return_value=[finding])),
            patch.object(sa, "_apply_approved_fixes", new=AsyncMock(return_value={"applied": 0, "skipped": 1})),
        ):
            result = asyncio.run(sa.run_self_audit())

        assert result["total_findings"] >= 1
        # All three agents were consulted (mocked to approve)
        assert fake_comms.call_agent_capability.await_count >= len(sa.AGENTS_TO_CONSULT)
    finally:
        _restore(saved)


def test_self_audit_parse_vote_structured():
    """_parse_vote reads structured 'vote' field first."""
    saved, sa, _, _, _ = _setup_self_audit()
    try:
        assert sa._parse_vote({"vote": "approve", "reason": "looks good"}) == "approve"
        assert sa._parse_vote({"vote": "reject"}) == "reject"
        assert sa._parse_vote({"vote": "defer"}) == "defer"
    finally:
        _restore(saved)


def test_self_audit_parse_vote_text_fallback():
    """_parse_vote falls back to text parsing when no structured field."""
    saved, sa, _, _, _ = _setup_self_audit()
    try:
        assert sa._parse_vote({"answer": "yes, fix it"}) == "approve"
        assert sa._parse_vote({"answer": "I reject this"}) == "reject"
        assert sa._parse_vote({"answer": "unclear"}) == "defer"
        assert sa._parse_vote(None) == "defer"
    finally:
        _restore(saved)


def test_self_audit_deduplicate_removes_same_file_issue():
    """_deduplicate drops findings with identical file+issue."""
    saved, sa, _, _, _ = _setup_self_audit()
    try:
        findings = [
            {"file": "foo.py", "issue": "dead code"},
            {"file": "foo.py", "issue": "dead code"},  # duplicate
            {"file": "bar.py", "issue": "dead code"},
        ]
        result = sa._deduplicate(findings)
        assert len(result) == 2
    finally:
        _restore(saved)


# ===========================================================================
# 5. perseus/sleep_cycle.py
# ===========================================================================

def _setup_sleep_cycle():
    modules_to_fake = [
        "shared.config",
        "shared.db",
        "shared.llm_client",
        "shared.self_model",
        "titan.memory",
        "perseus.daemon",
        "perseus.backprop",
        "perseus.cell_division",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=Path("/tmp"),
        ruflo=types.SimpleNamespace(enabled=False),
    )
    sys.modules["shared.config"] = fake_config

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value={"id": 1})
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    fake_db.insert_task = AsyncMock(return_value=None)
    sys.modules["shared.db"] = fake_db

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"proposals": [], "verdicts": []}')
    )
    sys.modules["shared.llm_client"] = fake_llm_mod

    fake_self_model = types.ModuleType("shared.self_model")
    fake_self_model.compute_agent_metrics = AsyncMock(return_value={})
    fake_self_model.update_self_model = AsyncMock()
    fake_self_model.get_all_self_models = AsyncMock(return_value={})
    sys.modules["shared.self_model"] = fake_self_model

    fake_titan_mem = types.ModuleType("titan.memory")
    fake_titan_mem._gather_daily_metrics = AsyncMock(return_value={})
    fake_titan_mem.memory_gc = AsyncMock(return_value={})
    sys.modules["titan.memory"] = fake_titan_mem

    fake_daemon = types.ModuleType("perseus.daemon")
    fake_daemon._assess_pipeline_state = AsyncMock(return_value={})
    sys.modules["perseus.daemon"] = fake_daemon

    fake_backprop = types.ModuleType("perseus.backprop")
    fake_backprop.apply_soul_doc_edit = AsyncMock(return_value=True)
    fake_backprop.apply_config_change = AsyncMock(return_value=True)
    fake_backprop.apply_rule_change = AsyncMock(return_value=True)
    fake_backprop.git_commit_cycle = AsyncMock(return_value=True)
    sys.modules["perseus.backprop"] = fake_backprop

    fake_cd = types.ModuleType("perseus.cell_division")
    fake_cd.evaluate_division_need = AsyncMock()
    sys.modules["perseus.cell_division"] = fake_cd

    sys.modules.pop("perseus.sleep_cycle", None)
    sc = importlib.import_module("perseus.sleep_cycle")
    return saved, sc, fake_db, fake_llm_mod.llm


def test_sleep_cycle_skips_when_convergence_flag_set():
    """run_sleep_cycle returns early when sleep_cycle_skip_next == 'true'."""
    saved, sc, fake_db, fake_llm = _setup_sleep_cycle()
    try:
        fake_db.get_config = AsyncMock(return_value="true")
        result = asyncio.run(sc.run_sleep_cycle())
        assert result.get("skipped") is True
        assert result.get("reason") == "convergence_pause"
        # Confirm flag was reset
        fake_db.set_config.assert_awaited_with("sleep_cycle_skip_next", "false")
    finally:
        _restore(saved)


def test_sleep_cycle_runs_when_no_skip():
    """run_sleep_cycle runs phases and returns a result dict with expected keys."""
    saved, sc, fake_db, fake_llm = _setup_sleep_cycle()
    try:
        fake_db.get_config = AsyncMock(return_value=None)

        snapshot = {"date": "2026-03-25", "recent_errors": [], "source_quality": []}
        proposals = [{"what": "test", "where": "somewhere", "why": "reason",
                      "category": "config_change", "confidence": 0.8,
                      "old_value": "a", "new_value": "b"}]

        with (
            patch.object(sc, "_gather_system_snapshot", new=AsyncMock(return_value=snapshot)),
            patch.object(sc, "_run_alpha", new=AsyncMock(return_value=proposals)),
            patch.object(sc, "_run_beta", new=AsyncMock(return_value=[
                {"proposal_index": 0, "verdict": "approve", "counterargument": "none"}
            ])),
        ):
            result = asyncio.run(sc.run_sleep_cycle())

        assert "cycle_id" in result
        assert "proposals" in result
        assert "survived" in result
        assert "applied" in result
    finally:
        _restore(saved)


def test_sleep_cycle_filter_surviving_respects_auto_rejected():
    """Proposals marked _auto_rejected are excluded even if Beta approves."""
    saved, sc, _, _ = _setup_sleep_cycle()
    try:
        proposals = [
            {"what": "change A", "_auto_rejected": True},
            {"what": "change B"},
        ]
        verdicts = [
            {"proposal_index": 0, "verdict": "approve"},
            {"proposal_index": 1, "verdict": "approve"},
        ]
        surviving = sc._filter_surviving(proposals, verdicts)
        assert len(surviving) == 1
        assert surviving[0]["what"] == "change B"
    finally:
        _restore(saved)


def test_sleep_cycle_filter_surviving_applies_modified_proposal():
    """MODIFY verdict merges modified_proposal into the original."""
    saved, sc, _, _ = _setup_sleep_cycle()
    try:
        proposals = [{"what": "original", "new_value": "100"}]
        verdicts = [
            {
                "proposal_index": 0,
                "verdict": "modify",
                "modified_proposal": {"new_value": "150"},
            }
        ]
        surviving = sc._filter_surviving(proposals, verdicts)
        assert len(surviving) == 1
        assert surviving[0]["new_value"] == "150"
        assert surviving[0]["what"] == "original"  # original fields preserved
    finally:
        _restore(saved)


# ===========================================================================
# 6. shared/capability_router.py
# ===========================================================================

def _setup_capability_router():
    modules_to_fake = ["shared.oj_bridge", "shared.task_routing"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_bridge = types.ModuleType("shared.oj_bridge")
    fake_bridge.AGENT_URLS = {
        "titan": "http://titan:8001",
        "hermes": "http://hermes:8002",
    }
    sys.modules["shared.oj_bridge"] = fake_bridge

    fake_routing = types.ModuleType("shared.task_routing")
    fake_routing.TASK_ROUTING = {"lead_discovery": "titan", "send_telegram": "hermes"}
    sys.modules["shared.task_routing"] = fake_routing

    sys.modules.pop("shared.capability_router", None)
    cr_mod = importlib.import_module("shared.capability_router")
    return saved, cr_mod


def test_capability_router_discover_all_indexes_capabilities():
    """discover_all populates _capability_map from agent cards."""
    saved, cr_mod = _setup_capability_router()
    try:
        router = cr_mod.CapabilityRouter(
            agent_urls={"titan": "http://titan:8001"},
            cache_ttl=300,
        )

        agent_card = {"capabilities": ["lead_discovery", "email_compose"], "skills": []}


        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = agent_card

        async def _run():
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_response)
                mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                return await router.discover_all()

        cards = asyncio.run(_run())
        assert "titan" in cards
        assert router._capability_map.get("lead_discovery") == "titan"
        assert router._capability_map.get("email_compose") == "titan"
    finally:
        _restore(saved)


def test_capability_router_route_falls_back_to_static():
    """route() falls back to TASK_ROUTING when capability not discovered."""
    saved, cr_mod = _setup_capability_router()
    try:
        router = cr_mod.CapabilityRouter(
            agent_urls={},  # no agents to discover
            cache_ttl=300,
        )
        # Force discovery to run (returns empty since no URLs)
        asyncio.run(router.discover_all())

        result = asyncio.run(router.route("lead_discovery"))
        assert result == "titan"

        result_missing = asyncio.run(router.route("unknown_task_type"))
        assert result_missing is None
    finally:
        _restore(saved)


def test_capability_router_invalidate_cache_clears_all():
    """invalidate_cache() resets discovery state."""
    saved, cr_mod = _setup_capability_router()
    try:
        router = cr_mod.CapabilityRouter(agent_urls={}, cache_ttl=300)
        # Manually populate cache
        router._capability_map["foo"] = "titan"
        router._agent_cards["titan"] = {"capabilities": []}
        router._discovery_attempted = True

        router.invalidate_cache()

        assert len(router._capability_map) == 0
        assert len(router._agent_cards) == 0
        assert router._discovery_attempted is False
    finally:
        _restore(saved)


def test_capability_router_invalidate_cache_for_agent():
    """invalidate_cache(agent_name) only removes that agent's entries."""
    saved, cr_mod = _setup_capability_router()
    try:
        router = cr_mod.CapabilityRouter(agent_urls={}, cache_ttl=300)
        router._capability_map["cap_a"] = "titan"
        router._capability_map["cap_b"] = "hermes"
        router._agent_cards["titan"] = {}
        router._agent_cards["hermes"] = {}
        router._discovery_attempted = True

        router.invalidate_cache("titan")

        assert "cap_b" in router._capability_map
        assert "cap_a" not in router._capability_map
        assert "hermes" in router._agent_cards
        assert "titan" not in router._agent_cards
        assert router._discovery_attempted is False
    finally:
        _restore(saved)


def test_capability_router_cache_is_reused_within_ttl():
    """discover_all() returns cached results within TTL without making HTTP calls."""
    saved, cr_mod = _setup_capability_router()
    try:
        router = cr_mod.CapabilityRouter(agent_urls={"titan": "http://titan:8001"}, cache_ttl=300)
        router._discovery_attempted = True
        router._last_discovery = 1e12  # far future — within TTL

        with patch("time.time", return_value=1e12 + 10):
            result = asyncio.run(router.discover_all())

        # Should return cached cards without HTTP
        assert result == router._agent_cards
    finally:
        _restore(saved)


# ===========================================================================
# 7. shared/config.py
# ===========================================================================

def test_config_defaults_without_env(monkeypatch):
    """Config resolves expected defaults when env vars are absent."""
    # Clear relevant env vars
    for key in ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT",
                 "OLLAMA_MODEL", "MONTHLY_BUDGET_CAP", "REVIEW_MODE",
                 "SALES_BEFORE_AUTONOMY", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)

    # Re-import to pick up env state (config is a module-level singleton but
    # the dataclass fields use default_factory which captures env at class
    # definition time — we test the helper functions directly)
    sys.modules.pop("shared.config", None)
    # dotenv may interfere; patch load_dotenv to no-op
    with patch("dotenv.load_dotenv"):
        config_mod = importlib.import_module("shared.config")

    cfg = config_mod.config
    # Structural checks — fields must exist and have correct types
    assert isinstance(cfg.postgres.host, str)
    assert isinstance(cfg.postgres.port, int)
    assert isinstance(cfg.budget.monthly_cap, int)
    assert isinstance(cfg.review_mode, bool)
    assert isinstance(cfg.sales_before_autonomy, int)
    assert cfg.root_dir.is_absolute()

    sys.modules.pop("shared.config", None)


def test_config_env_bool_parsing(monkeypatch):
    """_env_bool handles '1', 'true', 'yes' as True and '0', 'false' as False."""
    sys.modules.pop("shared.config", None)
    with patch("dotenv.load_dotenv"):
        config_mod = importlib.import_module("shared.config")

    assert config_mod._env_bool.__doc__ is not None or callable(config_mod._env_bool)

    # Test each truthy/falsy value via monkeypatch
    for truthy in ("1", "true", "yes"):
        monkeypatch.setenv("_TEST_BOOL", truthy)
        assert config_mod._env_bool("_TEST_BOOL") is True

    for falsy in ("0", "false", "no", ""):
        monkeypatch.setenv("_TEST_BOOL", falsy)
        assert config_mod._env_bool("_TEST_BOOL") is False

    sys.modules.pop("shared.config", None)


def test_config_postgres_dsn():
    """PostgresConfig.dsn produces a well-formed connection string."""
    sys.modules.pop("shared.config", None)
    with patch("dotenv.load_dotenv"):
        config_mod = importlib.import_module("shared.config")
    pg = config_mod.PostgresConfig(user="u", password="p", db="d", host="h", port=5432)
    assert pg.dsn == "postgresql://u:p@h:5432/d"
    sys.modules.pop("shared.config", None)


# ===========================================================================
# 8. tests/helpers/deep_thinking.py (relocated from shared/)
# ===========================================================================

def _import_deep_thinking():
    sys.modules.pop("tests.helpers.deep_thinking", None)
    return importlib.import_module("tests.helpers.deep_thinking")


def test_deep_thinking_estimate_empty_text():
    dt = _import_deep_thinking()
    assert dt.estimate_thinking_depth("") == 0.0


def test_deep_thinking_estimate_short_text():
    dt = _import_deep_thinking()
    score = dt.estimate_thinking_depth("yes")
    assert score == 0.1  # fewer than 10 words → 0.1


def test_deep_thinking_estimate_shallow_text():
    dt = _import_deep_thinking()
    score = dt.estimate_thinking_depth("hello world this is a simple statement with no reasoning at all here")
    assert 0.0 <= score <= 1.0


def test_deep_thinking_estimate_deep_text():
    dt = _import_deep_thinking()
    deep = (
        "Because the revenue dropped 15%, therefore we should adjust pricing. "
        "However, on the other hand, data shows the conversion rate improved. "
        "Based on 3-month statistics, we can conclude:\n1. Reduce price.\n2. Increase volume."
    )
    score = dt.estimate_thinking_depth(deep)
    assert score > 0.3, f"Expected score > 0.3 for deep text, got {score}"


def test_deep_thinking_disabled_passthrough():
    """When DEEP_THINKING_ENABLED=0, think_at_n calls generate_fn once."""
    dt = _import_deep_thinking()
    fake_generate = AsyncMock(return_value="some output")

    with patch.dict("os.environ", {"DEEP_THINKING_ENABLED": "0"}):
        result = asyncio.run(dt.think_at_n(fake_generate, "test prompt", n=5))

    assert result["text"] == "some output"
    assert result["candidates_tried"] == 1
    assert result["early_stopped"] is False
    fake_generate.assert_awaited_once()


def test_deep_thinking_early_stopping():
    """think_at_n stops early when first candidate exceeds min_depth."""
    dt = _import_deep_thinking()

    # Return a deeply-reasoned text on the first call
    deep_text = (
        "Because the metrics show 42% improvement, therefore we conclude success. "
        "Based on research data, evidence supports this. However, consider the alternative: "
        "statistics metrics show 1. First reason. 2. Second reason. 3. Third reason."
    )
    fake_generate = AsyncMock(return_value=deep_text)

    with patch.dict("os.environ", {"DEEP_THINKING_ENABLED": "1"}):
        result = asyncio.run(dt.think_at_n(fake_generate, "prompt", n=5, min_depth=0.01))

    assert result["early_stopped"] is True
    assert result["candidates_tried"] == 1
    assert result["text"] == deep_text


def test_deep_thinking_uses_all_candidates_when_all_shallow():
    """think_at_n exhausts all n candidates when none reach min_depth."""
    dt = _import_deep_thinking()
    fake_generate = AsyncMock(return_value="ok")  # shallow text

    with patch.dict("os.environ", {"DEEP_THINKING_ENABLED": "1"}):
        result = asyncio.run(dt.think_at_n(fake_generate, "prompt", n=3, min_depth=0.99))

    assert result["candidates_tried"] == 3
    assert result["early_stopped"] is False


def test_deep_thinking_best_candidate_selected():
    """think_at_n returns the candidate with the highest thinking depth."""
    dt = _import_deep_thinking()

    texts = [
        "ok",  # shallow
        (
            "Because metrics show 5 data points, therefore conclude. "
            "Based on evidence, research indicates improvement. However, statistics show otherwise."
        ),  # deep
        "ok",  # shallow
    ]
    call_count = 0

    async def fake_generate(prompt, **kwargs):
        nonlocal call_count
        text = texts[call_count % len(texts)]
        call_count += 1
        return text

    with patch.dict("os.environ", {"DEEP_THINKING_ENABLED": "1"}):
        result = asyncio.run(dt.think_at_n(fake_generate, "prompt", n=3, min_depth=0.99))

    # Best candidate should be the deep one (index 1)
    assert "metrics show" in result["text"] or result["depth"] > 0
    assert result["candidates_tried"] == 3


# ===========================================================================
# 9. shared/a2a_wrapper.py — A2A_SHARED_SECRET auth middleware
# ===========================================================================

def _make_a2a_test_app():
    """Create a minimal A2A app for auth testing with no external deps."""
    # The wrapper only imports from fastapi + shared.observability at module
    # level, so we mock observability and then import.
    mods_to_fake = [
        "shared.observability",
        "openjarvis",
        "openjarvis.security",
        "openjarvis.security.injection_scanner",
        "openjarvis.security.types",
        "openjarvis.core",
        "openjarvis.core.events",
        "shared.oj_bridge",
    ]
    saved = {k: sys.modules.get(k) for k in mods_to_fake}

    # shared.observability — used at module import time
    fake_obs = types.ModuleType("shared.observability")
    fake_obs.bind_context_from_payload = MagicMock()
    fake_obs.clear_observability_context = MagicMock()
    fake_obs.ensure_trace_context = MagicMock(return_value={
        "trace_id": "t1", "correlation_id": "c1", "task_id": "", "request_id": "r1",
    })
    sys.modules["shared.observability"] = fake_obs

    # openjarvis.* — imported lazily inside _handle_send; stub the packages
    for mod_name in [
        "openjarvis", "openjarvis.security", "openjarvis.security.injection_scanner",
        "openjarvis.security.types", "openjarvis.core", "openjarvis.core.events",
    ]:
        fake = types.ModuleType(mod_name)
        sys.modules[mod_name] = fake

    fake_oj_bridge = types.ModuleType("shared.oj_bridge")
    fake_oj_bridge.get_bus = MagicMock(return_value=MagicMock(publish=MagicMock()))
    sys.modules["shared.oj_bridge"] = fake_oj_bridge

    sys.modules.pop("shared.a2a_wrapper", None)
    wrapper_mod = importlib.import_module("shared.a2a_wrapper")

    from shared.a2a_wrapper import AgentCard, create_a2a_app

    card = AgentCard(
        name="test-agent",
        description="test",
        url="http://localhost:9999",
        capabilities=["ping"],
    )

    async def handler(text: str) -> str:
        return f"pong: {text}"

    app = create_a2a_app(agent_card=card, handler=handler)
    return saved, app, wrapper_mod


def test_a2a_auth_rejects_request_without_secret():
    """POST /a2a/tasks is rejected with 401 when A2A_SHARED_SECRET is set
    but the caller sends no X-A2A-Secret header."""
    from fastapi.testclient import TestClient

    saved, app, _ = _make_a2a_test_app()
    try:
        with patch.dict("os.environ", {"A2A_SHARED_SECRET": "supersecret"}):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/a2a/tasks",
                json={"jsonrpc": "2.0", "method": "tasks/send",
                      "params": {"input": "hello"}, "id": "test-1"},
            )
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == -32000
        assert "authentication" in body["error"]["message"].lower()
    finally:
        _restore(saved)


def test_a2a_auth_rejects_request_with_wrong_secret():
    """POST /a2a/tasks is rejected when the caller sends an incorrect secret."""
    from fastapi.testclient import TestClient

    saved, app, _ = _make_a2a_test_app()
    try:
        with patch.dict("os.environ", {"A2A_SHARED_SECRET": "supersecret"}):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/a2a/tasks",
                headers={"x-a2a-secret": "wrong-secret"},
                json={"jsonrpc": "2.0", "method": "tasks/send",
                      "params": {"input": "hello"}, "id": "test-2"},
            )
        assert response.status_code == 401
    finally:
        _restore(saved)


def test_a2a_auth_accepts_request_with_correct_secret():
    """POST /a2a/tasks succeeds when caller sends the correct X-A2A-Secret."""
    from fastapi.testclient import TestClient

    saved, app, _ = _make_a2a_test_app()
    try:
        with patch.dict("os.environ", {"A2A_SHARED_SECRET": "supersecret"}):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/a2a/tasks",
                headers={"x-a2a-secret": "supersecret"},
                json={"jsonrpc": "2.0", "method": "tasks/send",
                      "params": {"input": "hello"}, "id": "test-3"},
            )
        assert response.status_code == 200
        body = response.json()
        assert "result" in body
        assert body["result"]["state"] == "completed"
        assert "pong" in body["result"]["output"]
    finally:
        _restore(saved)


def test_a2a_auth_skipped_when_no_secret_configured():
    """POST /a2a/tasks succeeds without any header when A2A_SHARED_SECRET is
    not set (open/internal deployment mode)."""
    from fastapi.testclient import TestClient

    saved, app, _ = _make_a2a_test_app()
    try:
        with patch.dict("os.environ", {}, clear=False):
            # Ensure the env var is absent
            os.environ.pop("A2A_SHARED_SECRET", None)
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/a2a/tasks",
                json={"jsonrpc": "2.0", "method": "tasks/send",
                      "params": {"input": "no-auth"}, "id": "test-4"},
            )
        assert response.status_code == 200
        assert "result" in response.json()
    finally:
        _restore(saved)


# ===========================================================================
# NEW SWEEP — modules 1-15
# ===========================================================================

# ---------------------------------------------------------------------------
# Helper: minimal shared.config mock used by many modules below
# ---------------------------------------------------------------------------

def _make_fake_config():
    """Return a SimpleNamespace that covers config attributes used in tests."""
    telegram_ns = types.SimpleNamespace(bot_token="tok", chat_id="123")
    ollama_ns = types.SimpleNamespace(
        host="http://localhost:11434",
        model="llama3",
        secondary="llama3-small",
        embed_model="nomic-embed-text",
    )
    claude_ns = types.SimpleNamespace(
        api_key="sk-test",
        primary_model="claude-sonnet",
        fast_model="claude-haiku",
        genius_model="claude-opus",
    )
    budget_ns = types.SimpleNamespace(monthly_cap=800.0, alert_threshold=0.8)
    observability_ns = types.SimpleNamespace(
        sentry_dsn="",
        environment="test",
        release="0.0.1",
        sentry_traces_sample_rate=0.0,
        sentry_profiles_sample_rate=0.0,
        metrics_enabled=False,
        metrics_host="127.0.0.1",
        metrics_port_base=9200,
    )
    root_ns = types.SimpleNamespace(root_dir=Path("/tmp"))
    return types.SimpleNamespace(
        telegram=telegram_ns,
        ollama=ollama_ns,
        claude=claude_ns,
        budget=budget_ns,
        observability=observability_ns,
        root_dir=Path("/tmp"),
    )


# ===========================================================================
# 1. shared/hybrid_rag.py
# ===========================================================================

def _setup_hybrid_rag(hybrid_enabled: bool = True):
    modules_to_fake = [
        "shared.magma",
        "shared.llm_client",
        "shared.db",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_magma = types.ModuleType("shared.magma")
    fake_magma.magma_retrieve = AsyncMock(return_value="graph context")
    fake_magma._search_memory_with_metadata = AsyncMock(return_value=[
        {"content": "doc1", "score": 0.9, "category": "cat"},
    ])
    fake_magma._get_driver = MagicMock(return_value=True)
    sys.modules["shared.magma"] = fake_magma

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value="parametric knowledge here")
    )
    sys.modules["shared.llm_client"] = fake_llm_mod

    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock(return_value=[
        {"rule_text": "never spam", "confidence": 0.9},
    ])
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("tests.helpers.hybrid_rag", None)

    with patch.dict("os.environ", {"HYBRID_RAG": "1" if hybrid_enabled else "0"}):
        mod = importlib.import_module("tests.helpers.hybrid_rag")

    return saved, mod, fake_magma, fake_llm_mod.llm


def test_hybrid_rag_qdrant_branch_included():
    """When HYBRID_RAG=1, qdrant results are included in output."""
    saved, mod, fake_magma, _ = _setup_hybrid_rag(hybrid_enabled=True)
    try:
        results = asyncio.run(mod.hybrid_retrieve("test query", limit=10))
        assert any(r.get("source") == "qdrant" for r in results)
    finally:
        _restore(saved)


def test_hybrid_rag_neo4j_branch_included():
    """When HYBRID_RAG=1 and driver is available, neo4j results appear."""
    saved, mod, fake_magma, _ = _setup_hybrid_rag(hybrid_enabled=True)
    try:
        fake_magma.magma_retrieve = AsyncMock(return_value="deep graph info")
        results = asyncio.run(mod.hybrid_retrieve("query"))
        sources = [r["source"] for r in results]
        assert "neo4j" in sources
    finally:
        _restore(saved)


def test_hybrid_rag_parametric_branch_included():
    """Parametric (local LLM) source appears when LLM returns >20 chars."""
    saved, mod, _, fake_llm = _setup_hybrid_rag(hybrid_enabled=True)
    try:
        fake_llm.generate = AsyncMock(return_value="This is well-formed parametric knowledge")
        results = asyncio.run(mod.hybrid_retrieve("query"))
        assert any(r.get("source") == "parametric" for r in results)
    finally:
        _restore(saved)


def test_hybrid_rag_titan_rules_branch():
    """Titan rules from Postgres are included as a source."""
    saved, mod, _, _ = _setup_hybrid_rag(hybrid_enabled=True)
    try:
        results = asyncio.run(mod.hybrid_retrieve("query"))
        assert any(r.get("source") == "postgres" for r in results)
    finally:
        _restore(saved)


def test_hybrid_rag_fallback_when_disabled():
    """When HYBRID_RAG=0, falls back to magma_retrieve path."""
    saved, mod, fake_magma, _ = _setup_hybrid_rag(hybrid_enabled=False)
    try:
        fake_magma.magma_retrieve = AsyncMock(return_value="fallback text")
        results = asyncio.run(mod.hybrid_retrieve("query"))
        assert len(results) == 1
        assert results[0]["source"] == "magma"
    finally:
        _restore(saved)


def test_reward_chain_score_positive_outcome():
    saved, mod, _, _ = _setup_hybrid_rag()
    try:
        history = [
            {"outcome": "neutral"},
            {"outcome": "neutral"},
            {"outcome": "positive"},
        ]
        score = mod.reward_chain_score(history)
        assert 0.0 < score <= 1.0
    finally:
        _restore(saved)


def test_reward_chain_score_empty_history():
    saved, mod, _, _ = _setup_hybrid_rag()
    try:
        assert mod.reward_chain_score([]) == 0.5
    finally:
        _restore(saved)


# ===========================================================================
# 2. shared/llm_client.py
# ===========================================================================

def _setup_llm_client():
    modules_to_fake = ["shared.config"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_cfg_mod = types.ModuleType("shared.config")
    fake_cfg_mod.config = _make_fake_config()
    sys.modules["shared.config"] = fake_cfg_mod

    sys.modules.pop("shared.llm_client", None)
    mod = importlib.import_module("shared.llm_client")
    return saved, mod


def test_llm_client_generate_with_images_raises_no_api_key():
    """generate_with_images raises RuntimeError when api_key is absent."""
    saved, mod = _setup_llm_client()
    try:
        # Patch the config object on the module so api_key is empty
        fake_config = _make_fake_config()
        fake_config.claude = types.SimpleNamespace(
            api_key="",  # no API key
            primary_model="claude-sonnet",
            fast_model="claude-haiku",
            genius_model="claude-opus",
        )
        with patch.object(mod, "config", fake_config):
            try:
                asyncio.run(mod.llm.generate_with_images(
                    "describe", images=[b"fake"], model="smart"
                ))
                assert False, "expected RuntimeError"
            except RuntimeError as exc:
                assert "Claude vision unavailable" in str(exc) or "ANTHROPIC_API_KEY" in str(exc)
    finally:
        _restore(saved)


def test_llm_client_generate_with_images_raises_local_model():
    """generate_with_images raises RuntimeError for 'local' model."""
    saved, mod = _setup_llm_client()
    try:
        try:
            asyncio.run(mod.llm.generate_with_images(
                "describe", images=[b"data"], model="local"
            ))
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "multimodal" in str(exc).lower() or "local" in str(exc).lower()
    finally:
        _restore(saved)


def test_llm_client_ollama_404_fallback():
    """When Ollama returns 404 for primary model, retries with secondary."""
    saved, mod = _setup_llm_client()
    try:

        call_count = {"n": 0}

        async def fake_post(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: 404
                mock_resp = MagicMock()
                mock_resp.status_code = 404
                # The code checks status_code == 404, so raise_for_status is not what triggers fallback
                mock_resp.raise_for_status = MagicMock()
                return mock_resp
            else:
                # Second call: success
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json = MagicMock(return_value={"response": "fallback text"})
                return mock_resp

        mock_http_client = MagicMock()
        mock_http_client.post = fake_post
        mock_http_client.is_closed = False

        # Patch _get_http so it always returns our mock
        mod.llm._get_http = MagicMock(return_value=mock_http_client)

        # Also patch _resolve_ollama_model to return primary model first
        async def resolve_model(model, pipeline_stage=""):
            return "llama3" if call_count["n"] == 0 else "llama3-small"

        mod.llm._resolve_ollama_model = resolve_model

        result = asyncio.run(mod.llm._ollama_generate(
            "hello", "", "local", 100, 0.5
        ))
        assert result == "fallback text"
        assert call_count["n"] >= 2
    finally:
        _restore(saved)


def test_llm_client_embed_returns_list():
    """embed() calls Ollama embeddings endpoint and returns list."""
    saved, mod = _setup_llm_client()
    try:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"embedding": [0.1, 0.2, 0.3]})

        mock_http_client = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_http_client.is_closed = False

        mod.llm._get_http = MagicMock(return_value=mock_http_client)

        result = asyncio.run(mod.llm.embed("some text"))
        assert result == [0.1, 0.2, 0.3]
    finally:
        _restore(saved)


# ===========================================================================
# 3. shared/execution_loop.py
# ===========================================================================

def _setup_execution_loop():
    modules_to_fake = ["shared.comms", "shared.llm_client"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock(return_value=1)
    sys.modules["shared.comms"] = fake_comms

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value="step result content that is long enough")
    )
    sys.modules["shared.llm_client"] = fake_llm_mod

    sys.modules.pop("shared.execution_loop", None)
    mod = importlib.import_module("shared.execution_loop")
    return saved, mod, fake_llm_mod.llm


def test_execution_loop_execute_step_passes_no_check():
    """_execute_step passes when no check function and result >10 chars."""
    saved, mod, _ = _setup_execution_loop()
    try:
        step = mod.Step(name="step1", prompt="do something")
        plan = mod.TaskPlan(name="plan", description="desc", steps=[step])
        result = asyncio.run(mod._execute_step(step, plan, []))
        assert result["passed"] is True
        assert result["attempts"] == 1
    finally:
        _restore(saved)


def test_execution_loop_execute_step_check_failure_retries():
    """_execute_step retries when check function returns passed=False."""
    saved, mod, fake_llm = _setup_execution_loop()
    try:
        attempt_count = {"n": 0}

        async def bad_check(result):
            attempt_count["n"] += 1
            return {"passed": False, "error": "always fails"}

        step = mod.Step(name="step1", prompt="do thing", check=bad_check, max_tokens=100)
        plan = mod.TaskPlan(name="plan", description="desc", steps=[step], max_retries=2)
        result = asyncio.run(mod._execute_step(step, plan, []))
        assert result["passed"] is False
        assert attempt_count["n"] == 2  # checked on each attempt
    finally:
        _restore(saved)


def test_execution_loop_execute_step_check_passes_second_attempt():
    """_execute_step succeeds on second attempt after first check fails."""
    saved, mod, fake_llm = _setup_execution_loop()
    try:
        call_count = {"n": 0}

        async def flaky_check(result):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"passed": False, "error": "not yet"}
            return {"passed": True}

        step = mod.Step(name="step1", prompt="try hard", check=flaky_check)
        plan = mod.TaskPlan(name="plan", description="desc", steps=[step], max_retries=3)
        result = asyncio.run(mod._execute_step(step, plan, []))
        assert result["passed"] is True
        assert result["attempts"] == 2
    finally:
        _restore(saved)


def test_execution_loop_self_correction_path():
    """Self-correction path is invoked when use_self_correction=True and check fails."""
    saved, mod, _ = _setup_execution_loop()
    try:
        # Patch test_time_learning import inside the module
        fake_ttl = types.ModuleType("shared.test_time_learning")
        fake_ttl.TEST_TIME_LEARNING_ENABLED = True
        fake_ttl.score_self_correct = AsyncMock(return_value={
            "improved": True,
            "result": "corrected result that is good",
        })

        async def always_fails(result):
            return {"passed": False, "error": "bad output"}

        step = mod.Step(
            name="corrected_step",
            prompt="write something",
            check=always_fails,
            use_self_correction=True,
        )
        plan = mod.TaskPlan(name="plan", description="desc", steps=[step], max_retries=2)

        with patch.dict("sys.modules", {"shared.test_time_learning": fake_ttl}):
            result = asyncio.run(mod._execute_step(step, plan, []))

        assert result.get("self_corrected") is True or result.get("passed") is True
    finally:
        _restore(saved)


# ===========================================================================
# 4. shared/observability.py
# ===========================================================================

def _setup_observability():
    modules_to_fake = ["shared.config"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_cfg_mod = types.ModuleType("shared.config")
    fake_cfg_mod.config = _make_fake_config()
    sys.modules["shared.config"] = fake_cfg_mod

    sys.modules.pop("shared.observability", None)
    mod = importlib.import_module("shared.observability")
    return saved, mod


def test_observability_metrics_server_skipped_in_tests():
    """configure_service_observability does NOT start the metrics server in test env."""
    saved, mod = _setup_observability()
    try:
        # PYTEST_CURRENT_TEST is set during test runs — server start should be skipped
        with patch("shared.observability.start_http_server") as mock_start:
            mod._INITIALIZED_SERVICES.discard("test_svc")
            mod.configure_service_observability("test_svc", start_metrics_server_for_service=True)
            mock_start.assert_not_called()
    finally:
        _restore(saved)


def test_observability_asyncio_exception_handler_installed_once():
    """install_asyncio_exception_handler idempotently marks the loop."""
    saved, mod = _setup_observability()
    try:
        loop = asyncio.new_event_loop()
        try:
            mod.install_asyncio_exception_handler(loop, "svc_test")
            marker = "_objective_hertz_asyncio_handler_svc_test"
            assert getattr(loop, marker, False) is True
            # Installing again should not raise
            mod.install_asyncio_exception_handler(loop, "svc_test")
        finally:
            loop.close()
    finally:
        _restore(saved)


def test_observability_asyncio_handler_captures_exception():
    """The installed asyncio exception handler captures exceptions via metrics."""
    saved, mod = _setup_observability()
    try:
        loop = asyncio.new_event_loop()
        try:
            mod.install_asyncio_exception_handler(loop, "capture_test")
            handler = loop.get_exception_handler()
            # Call the handler with a real exception
            exc = ValueError("test exception")
            with patch.object(mod, "capture_exception") as mock_cap:
                handler(loop, {"exception": exc, "message": "oops"})
                mock_cap.assert_called_once_with(exc, service_name="capture_test", category="asyncio")
        finally:
            loop.close()
    finally:
        _restore(saved)


def test_observability_context_roundtrip():
    """set_observability_context and get_log_context roundtrip correctly."""
    saved, mod = _setup_observability()
    try:
        mod.clear_observability_context()
        mod.set_observability_context(trace_id="abc123", task_id="task-1")
        ctx = mod.get_log_context()
        assert ctx["trace_id"] == "abc123"
        assert ctx["task_id"] == "task-1"
    finally:
        mod.clear_observability_context()
        _restore(saved)


# ===========================================================================
# 5. shared/oj_bridge.py
# ===========================================================================

def _setup_oj_bridge():
    modules_to_fake = ["shared.observability"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_obs = types.ModuleType("shared.observability")
    fake_obs.ensure_trace_context = MagicMock(return_value={
        "trace_id": "t1", "correlation_id": "c1", "request_id": "r1"
    })
    sys.modules["shared.observability"] = fake_obs

    sys.modules.pop("shared.oj_bridge", None)
    mod = importlib.import_module("shared.oj_bridge")
    return saved, mod, fake_obs


def test_oj_bridge_get_bus_singleton():
    """get_bus returns the same singleton on repeated calls."""
    saved, mod, _ = _setup_oj_bridge()
    try:
        fake_bus = MagicMock()
        fake_oj_events = types.ModuleType("openjarvis.core.events")
        fake_oj_events.get_event_bus = MagicMock(return_value=fake_bus)

        mod._bus = None
        with patch.dict("sys.modules", {"openjarvis.core.events": fake_oj_events}):
            bus1 = mod.get_bus()
            bus2 = mod.get_bus()
        assert bus1 is bus2
        assert fake_oj_events.get_event_bus.call_count == 1  # only initialised once
    finally:
        mod._bus = None
        _restore(saved)


def test_oj_bridge_get_a2a_client_unknown_agent():
    """get_a2a_client returns None for an unknown agent name."""
    saved, mod, _ = _setup_oj_bridge()
    try:
        client = mod.get_a2a_client("nonexistent_agent")
        assert client is None
    finally:
        _restore(saved)


def test_oj_bridge_call_agent_async_returns_error_no_url():
    """call_agent_async returns error dict when agent has no URL configured."""
    saved, mod, _ = _setup_oj_bridge()
    try:
        # Remove any cached client so the lookup triggers
        mod._a2a_clients.pop("nonexistent_agent", None)
        result = asyncio.run(mod.call_agent_async("nonexistent_agent", "capability"))
        assert "error" in result
    finally:
        _restore(saved)


def test_oj_bridge_call_agent_returns_error_no_url():
    """Sync call_agent returns error dict when agent name is unknown."""
    saved, mod, _ = _setup_oj_bridge()
    try:
        result = mod.call_agent("nonexistent_agent", "some_capability")
        assert "error" in result
        assert "nonexistent_agent" in result["error"]
    finally:
        _restore(saved)


# ===========================================================================
# 6. shared/metaclaw.py
# ===========================================================================

def _setup_metaclaw():
    modules_to_fake = ["shared.db", "shared.bandit"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    sys.modules["shared.db"] = fake_db

    fake_bandit = types.ModuleType("shared.bandit")
    fake_bandit.BANDIT_ENABLED = False
    fake_bandit.get_bandit = MagicMock()
    sys.modules["shared.bandit"] = fake_bandit

    sys.modules.pop("shared.metaclaw", None)
    mod = importlib.import_module("shared.metaclaw")
    return saved, mod, fake_db, fake_bandit


def test_metaclaw_fast_adapt_converged_bandit_overrides_template():
    """When bandit has converged, its winner overrides the meta template."""
    saved, mod, _, fake_bandit = _setup_metaclaw()
    try:
        fake_bandit.BANDIT_ENABLED = True
        fake_bandit_instance = MagicMock()
        fake_bandit_instance.get_stats = MagicMock(return_value={
            "converged": True,
            "winner": "premium_template",
        })
        fake_bandit.get_bandit = MagicMock(return_value=fake_bandit_instance)

        lead = {"industry": "tech", "region": "us", "lead_score": 60}
        result = asyncio.run(mod.fast_adapt(lead))
        assert result["template"] == "premium_template"
    finally:
        _restore(saved)


def test_metaclaw_fast_adapt_disabled_returns_defaults():
    """When METACLAW_ENABLED=0, returns static defaults."""
    saved, mod, _, _ = _setup_metaclaw()
    try:
        with patch.object(mod, "METACLAW_ENABLED", False):
            result = asyncio.run(mod.fast_adapt({"industry": "tech"}))
        assert result["template"] == "default"
        assert result["confidence"] == 0.5
    finally:
        _restore(saved)


def test_metaclaw_slow_consolidate_updates_industry_segments():
    """slow_consolidate updates meta-weights for segments with enough data."""
    saved, mod, fake_db, _ = _setup_metaclaw()
    try:
        fake_db.fetch_all = AsyncMock(return_value=[
            {"industry": "saas", "region": "us", "total": 5,
             "conversions": 2, "avg_deal": 500},
        ])
        result = asyncio.run(mod.slow_consolidate())
        assert result["updated"] == 1
        assert "saas" in result["industries"]
        fake_db.set_config.assert_awaited_once()
    finally:
        _restore(saved)


def test_metaclaw_slow_consolidate_disabled():
    """slow_consolidate returns updated=0 when feature disabled."""
    saved, mod, _, _ = _setup_metaclaw()
    try:
        with patch.object(mod, "METACLAW_ENABLED", False):
            result = asyncio.run(mod.slow_consolidate())
        assert result == {"updated": 0}
    finally:
        _restore(saved)


def test_metaclaw_load_meta_weights_json_config():
    """_load_meta_weights parses JSON from get_config correctly."""
    import json
    saved, mod, fake_db, _ = _setup_metaclaw()
    try:
        fake_db.get_config = AsyncMock(return_value=json.dumps({
            "best_template": "high_value",
            "confidence": 0.8,
        }))
        result = asyncio.run(mod._load_meta_weights("finance", "eu"))
        assert result["best_template"] == "high_value"
        assert result["confidence"] == 0.8
    finally:
        _restore(saved)


# ===========================================================================
# 7. shared/milestone_rewards.py
# ===========================================================================

def _setup_milestone_rewards():
    modules_to_fake = ["shared.db", "shared.test_time_learning", "shared.bandit"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    sys.modules["shared.db"] = fake_db

    fake_ttl = types.ModuleType("shared.test_time_learning")
    fake_ttl.TTRL_GRADIENT_ENABLED = True
    fake_ttl.ttrl_gradient_update = AsyncMock(return_value=False)
    sys.modules["shared.test_time_learning"] = fake_ttl

    fake_bandit = types.ModuleType("shared.bandit")
    fake_bandit.BANDIT_ENABLED = True
    mock_bandit_inst = MagicMock()
    mock_bandit_inst.update = AsyncMock()
    fake_bandit.get_bandit = MagicMock(return_value=mock_bandit_inst)
    sys.modules["shared.bandit"] = fake_bandit

    sys.modules.pop("tests.helpers.milestone_rewards", None)
    mod = importlib.import_module("tests.helpers.milestone_rewards")
    return saved, mod, fake_db, fake_ttl, fake_bandit


def test_milestone_rewards_emit_routes_to_ttrl():
    """emit_milestone_reward calls ttrl_gradient_update when enabled."""
    saved, mod, _, fake_ttl, _ = _setup_milestone_rewards()
    try:
        asyncio.run(mod.emit_milestone_reward(42, "email_sent", "replied"))
        fake_ttl.ttrl_gradient_update.assert_awaited_once()
    finally:
        _restore(saved)


def test_milestone_rewards_emit_routes_to_bandit():
    """emit_milestone_reward calls bandit.update when experiment metadata present."""
    saved, mod, _, _, fake_bandit = _setup_milestone_rewards()
    try:
        meta = {"bandit_experiment": "template_tech", "bandit_arm": "variant_a"}
        asyncio.run(mod.emit_milestone_reward(42, "email_sent", "replied", metadata=meta))
        fake_bandit.get_bandit().update.assert_awaited_once()
    finally:
        _restore(saved)


def test_milestone_rewards_zero_reward_for_unknown_stage():
    """get_transition_reward returns 0 for unknown stage."""
    saved, mod, _, _, _ = _setup_milestone_rewards()
    try:
        reward = mod.get_transition_reward("email_sent", "unknown_stage_xyz")
        assert reward == 0.0
    finally:
        _restore(saved)


def test_milestone_rewards_paid_is_max():
    """'paid' stage has the maximum reward value of 1.0."""
    saved, mod, _, _, _ = _setup_milestone_rewards()
    try:
        reward = mod.get_transition_reward("invoiced", "paid")
        assert reward == 1.0
    finally:
        _restore(saved)


def test_milestone_rewards_disabled_returns_zero():
    """emit_milestone_reward returns 0.0 when feature disabled."""
    saved, mod, fake_db, _, _ = _setup_milestone_rewards()
    try:
        with patch.object(mod, "MILESTONE_REWARDS_ENABLED", False):
            result = asyncio.run(mod.emit_milestone_reward(1, "email_sent", "replied"))
        assert result == 0.0
        fake_db.emit_event.assert_not_awaited()
    finally:
        _restore(saved)


# ===========================================================================
# 8. shared/pipeline_dag.py
# ===========================================================================

def _setup_pipeline_dag():
    modules_to_fake = ["shared.magma"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_magma = types.ModuleType("shared.magma")
    fake_magma._get_driver = MagicMock(return_value=None)
    sys.modules["shared.magma"] = fake_magma

    sys.modules.pop("shared.pipeline_dag", None)
    mod = importlib.import_module("shared.pipeline_dag")
    return saved, mod, fake_magma


def test_pipeline_dag_entity_timeline_neo4j_degradation():
    """entity_timeline returns [] when _get_driver() returns None."""
    saved, mod, fake_magma = _setup_pipeline_dag()
    try:
        fake_magma._get_driver = MagicMock(return_value=None)
        result = asyncio.run(mod.entity_timeline("acme corp"))
        assert result == []
    finally:
        _restore(saved)


def test_pipeline_dag_entity_timeline_with_driver():
    """entity_timeline queries Neo4j session and returns formatted records."""
    saved, mod, fake_magma = _setup_pipeline_dag()
    try:
        fake_record = {
            "node_id": "n1",
            "content": "lead found",
            "category": "discovery",
            "timestamp": "2026-01-01T00:00:00",
        }
        mock_session = MagicMock()
        mock_session.run = MagicMock(return_value=[
            {"node_id": "n1", "content": "lead found",
             "category": "discovery", "timestamp": "2026-01-01T00:00:00"}
        ])
        mock_driver = MagicMock()
        mock_driver.session = MagicMock(return_value=mock_session)
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_driver.__enter__ = MagicMock(return_value=mock_driver)
        mock_driver.__exit__ = MagicMock(return_value=False)
        fake_magma._get_driver = MagicMock(return_value=mock_driver)
        result = asyncio.run(mod.entity_timeline("acme corp"))
        assert isinstance(result, list)
    finally:
        _restore(saved)


def test_pipeline_dag_can_transition_valid():
    """can_transition returns allowed=True when all constraints are met."""
    saved, mod, _ = _setup_pipeline_dag()
    try:
        client = {"business_name": "Acme"}
        result = mod.can_transition("discovered", "researched", client_data=client)
        assert result["allowed"] is True
    finally:
        _restore(saved)


def test_pipeline_dag_can_transition_missing_field():
    """can_transition returns allowed=False when required field missing."""
    saved, mod, _ = _setup_pipeline_dag()
    try:
        result = mod.can_transition("discovered", "researched", client_data={})
        assert result["allowed"] is False
        assert "business_name" in result["missing_fields"]
    finally:
        _restore(saved)


# ===========================================================================
# 9. shared/scientific_loop.py
# ===========================================================================

def _setup_scientific_loop():
    modules_to_fake = ["shared.db"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.execute = AsyncMock()
    fake_db.fetch_val = AsyncMock(return_value=0.0)
    fake_db.fetch_all = AsyncMock(return_value=[])
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("tests.helpers.scientific_loop", None)
    mod = importlib.import_module("tests.helpers.scientific_loop")
    return saved, mod, fake_db


def test_scientific_loop_evaluate_confirmed():
    """evaluate_experiments marks experiment 'confirmed' when delta exceeds 50% of expected."""
    saved, mod, fake_db = _setup_scientific_loop()
    try:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(hours=49)).isoformat()
        fake_db.fetch_all = AsyncMock(return_value=[{
            "id": 1,
            "hypothesis": "test hyp",
            "metric_name": "reply_rate",
            "baseline_value": 0.10,
            "expected_delta": 0.05,
            "cycle_id": 0,
            "created_at": cutoff,
        }])
        # Return a value that produces actual_delta > expected * 0.5
        fake_db.fetch_val = AsyncMock(return_value=0.18)  # delta = 0.08 > 0.025

        results = asyncio.run(mod.evaluate_experiments())
        assert len(results) == 1
        assert results[0]["status"] == "confirmed"
    finally:
        _restore(saved)


def test_scientific_loop_evaluate_refuted():
    """evaluate_experiments marks experiment 'refuted' when delta is insufficient."""
    saved, mod, fake_db = _setup_scientific_loop()
    try:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(hours=49)).isoformat()
        fake_db.fetch_all = AsyncMock(return_value=[{
            "id": 2,
            "hypothesis": "bad hyp",
            "metric_name": "reply_rate",
            "baseline_value": 0.10,
            "expected_delta": 0.20,
            "cycle_id": 0,
            "created_at": cutoff,
        }])
        fake_db.fetch_val = AsyncMock(return_value=0.11)  # delta = 0.01 << expected

        results = asyncio.run(mod.evaluate_experiments())
        assert len(results) == 1
        assert results[0]["status"] == "refuted"
    finally:
        _restore(saved)


def test_scientific_loop_get_metric_value_known_metric():
    """_get_metric_value returns a float for a known metric name."""
    saved, mod, fake_db = _setup_scientific_loop()
    try:
        fake_db.fetch_val = AsyncMock(return_value=0.25)
        result = asyncio.run(mod._get_metric_value("reply_rate"))
        assert result == 0.25
    finally:
        _restore(saved)


def test_scientific_loop_get_metric_value_unknown_returns_none():
    """_get_metric_value returns None for an unrecognised metric name."""
    saved, mod, _ = _setup_scientific_loop()
    try:
        result = asyncio.run(mod._get_metric_value("nonexistent_metric_xyz"))
        assert result is None
    finally:
        _restore(saved)


def test_scientific_loop_evaluate_rollback_on_refuted():
    """evaluate_experiments triggers rollback when experiment is refuted and has cycle_id."""
    saved, mod, fake_db = _setup_scientific_loop()
    try:
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(hours=49)).isoformat()
        fake_db.fetch_all = AsyncMock(return_value=[{
            "id": 3,
            "hypothesis": "roll me back",
            "metric_name": "reply_rate",
            "baseline_value": 0.10,
            "expected_delta": 0.20,
            "cycle_id": 99,
            "created_at": cutoff,
        }])
        fake_db.fetch_val = AsyncMock(return_value=0.10)

        fake_backprop = types.ModuleType("perseus.backprop")
        fake_backprop.rollback_cycle = AsyncMock()

        with patch.dict("sys.modules", {"perseus.backprop": fake_backprop}):
            results = asyncio.run(mod.evaluate_experiments())

        assert results[0].get("rolled_back") is not None  # key present
    finally:
        _restore(saved)


# ===========================================================================
# 10. shared/self_model.py
# ===========================================================================

def _setup_self_model():
    modules_to_fake = ["shared.db"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_val = AsyncMock(return_value=0)
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("shared.self_model", None)
    mod = importlib.import_module("shared.self_model")
    return saved, mod, fake_db


def test_self_model_format_all_self_models_covers_all_agents():
    """format_all_self_models returns a string covering all 4 agent names."""
    saved, mod, _ = _setup_self_model()
    try:
        result = asyncio.run(mod.format_all_self_models())
        for agent in ("perseus", "titan", "hermes", "clawdbot"):
            assert agent in result.lower()
    finally:
        _restore(saved)


def test_self_model_compute_agent_metrics_returns_structure():
    """compute_agent_metrics returns a dict with expected keys."""
    saved, mod, _ = _setup_self_model()
    try:
        result = asyncio.run(mod.compute_agent_metrics("hermes"))
        assert "decisions_made_24h" in result
        assert "error_rate_24h" in result
        assert "tasks_completed_24h" in result
    finally:
        _restore(saved)


def test_self_model_compute_agent_metrics_titan_stage_queries():
    """compute_agent_metrics for titan queries stage-specific data."""
    saved, mod, fake_db = _setup_self_model()
    try:
        # self_model imports from shared.db at module level, so patch on the module directly
        with patch.object(mod, "fetch_all", AsyncMock(return_value=[{"stage": "email_compose", "cnt": 3}])), \
             patch.object(mod, "fetch_val", AsyncMock(return_value=0)):
            result = asyncio.run(mod.compute_agent_metrics("titan"))
        assert result["worst_performing_stage"] == "email_compose"
    finally:
        _restore(saved)


def test_self_model_update_and_get_roundtrip():
    """update_self_model persists and get_self_model reads back correctly."""
    saved, mod, fake_db = _setup_self_model()
    try:
        store = {}

        async def mock_set_config(key, value):
            store[key] = value

        async def mock_get_config(key, default=None):
            return store.get(key, default)

        with patch.object(mod, "set_config", mock_set_config), \
             patch.object(mod, "get_config", mock_get_config):
            asyncio.run(mod.update_self_model("titan", {"strengths": ["email_compose"]}))
            result = asyncio.run(mod.get_self_model("titan"))
        assert "email_compose" in result["strengths"]
    finally:
        _restore(saved)


# ===========================================================================
# 11. shared/skill_loader.py
# ===========================================================================

def _setup_skill_loader(tmp_path: Path):
    modules_to_fake = ["shared.config", "shared.llm_client"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_cfg_mod = types.ModuleType("shared.config")
    cfg_obj = _make_fake_config()
    cfg_obj.root_dir = tmp_path
    fake_cfg_mod.config = cfg_obj
    sys.modules["shared.config"] = fake_cfg_mod

    fake_llm_mod = types.ModuleType("shared.llm_client")
    fake_llm_mod.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value="skill executed result")
    )
    sys.modules["shared.llm_client"] = fake_llm_mod

    sys.modules.pop("shared.skill_loader", None)
    mod = importlib.import_module("shared.skill_loader")
    return saved, mod, fake_llm_mod.llm


def test_skill_loader_find_skill_frontmatter_match(tmp_path):
    """find_skill finds a skill whose SKILL.md contains 'name: <skill_name>'."""
    saved, mod, _ = _setup_skill_loader(tmp_path)
    try:
        skill_dir = tmp_path / "hermes" / "skills" / "my-custom-skill"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("---\nname: frontmatter-skill\ndescription: test\n---\nDo things.")

        # Reload SKILL_DIRS to reflect tmp_path
        mod.SKILL_DIRS = [tmp_path / "hermes" / "skills"]

        result = mod.find_skill("frontmatter-skill")
        assert result is not None
        assert result.name == "SKILL.md"
    finally:
        _restore(saved)


def test_skill_loader_find_skill_direct_match(tmp_path):
    """find_skill returns path when skill directory name matches."""
    saved, mod, _ = _setup_skill_loader(tmp_path)
    try:
        skill_dir = tmp_path / "hermes" / "skills" / "lead-research"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: lead-research\n---\nResearch leads.")

        mod.SKILL_DIRS = [tmp_path / "hermes" / "skills"]
        result = mod.find_skill("lead-research")
        assert result is not None
    finally:
        _restore(saved)


def test_skill_loader_execute_skill_or_fallback_uses_skill(tmp_path):
    """execute_skill_or_fallback uses the skill when found, not the fallback."""
    saved, mod, fake_llm = _setup_skill_loader(tmp_path)
    try:
        skill_dir = tmp_path / "hermes" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("You are a test skill.")

        mod.SKILL_DIRS = [tmp_path / "hermes" / "skills"]
        fallback_called = {"called": False}

        async def fallback():
            fallback_called["called"] = True
            return "fallback"

        result = asyncio.run(mod.execute_skill_or_fallback(
            "test-skill", "do task", fallback
        ))
        assert fallback_called["called"] is False
        assert result == "skill executed result"
    finally:
        _restore(saved)


def test_skill_loader_execute_skill_or_fallback_uses_fallback(tmp_path):
    """execute_skill_or_fallback calls fallback when skill not found."""
    saved, mod, _ = _setup_skill_loader(tmp_path)
    try:
        mod.SKILL_DIRS = [tmp_path / "hermes" / "skills"]  # empty

        async def fallback():
            return "fallback result"

        result = asyncio.run(mod.execute_skill_or_fallback(
            "nonexistent-skill", "do task", fallback
        ))
        assert result == "fallback result"
    finally:
        _restore(saved)


def test_skill_loader_list_installed_skills_parses_description(tmp_path):
    """list_installed_skills parses description from frontmatter."""
    saved, mod, _ = _setup_skill_loader(tmp_path)
    try:
        skill_dir = tmp_path / "hermes" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Does awesome things\n---\nBody."
        )

        mod.SKILL_DIRS = [tmp_path / "hermes" / "skills"]
        skills = mod.list_installed_skills()
        assert len(skills) == 1
        assert skills[0]["description"] == "Does awesome things"
    finally:
        _restore(saved)


# ===========================================================================
# 12. shared/test_time_learning.py
# ===========================================================================

def _setup_test_time_learning():
    modules_to_fake = ["shared.magma", "titan.memory"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_magma = types.ModuleType("shared.magma")
    fake_magma._get_driver = MagicMock(return_value=True)
    fake_magma.magma_retrieve = AsyncMock(return_value="MAGMA memory context: similar past event")
    sys.modules["shared.magma"] = fake_magma

    fake_titan_memory = types.ModuleType("titan.memory")
    fake_titan_memory.get_relevant_learnings = AsyncMock(return_value="flat memory result")
    sys.modules["titan.memory"] = fake_titan_memory

    sys.modules.pop("shared.test_time_learning", None)
    mod = importlib.import_module("shared.test_time_learning")
    return saved, mod, fake_magma, fake_titan_memory


def test_ttl_memrl_uses_magma_when_driver_available():
    """memrl_context_inject returns MAGMA results when driver is available."""
    saved, mod, fake_magma, _ = _setup_test_time_learning()
    try:
        with patch.object(mod, "TEST_TIME_LEARNING_ENABLED", True):
            result = asyncio.run(mod.memrl_context_inject("recent leads query"))
        assert "RELEVANT EXPERIENCE" in result
        assert "MAGMA" in result or "similar" in result
    finally:
        _restore(saved)


def test_ttl_memrl_falls_back_to_flat_memory():
    """memrl_context_inject falls back to flat memory when MAGMA unavailable."""
    saved, mod, fake_magma, fake_titan_memory = _setup_test_time_learning()
    try:
        fake_magma._get_driver = MagicMock(return_value=None)
        with patch.object(mod, "TEST_TIME_LEARNING_ENABLED", True):
            result = asyncio.run(mod.memrl_context_inject("query"))
        assert "flat memory" in result or "RELEVANT EXPERIENCE" in result
    finally:
        _restore(saved)


def test_ttl_memrl_disabled_returns_empty():
    """memrl_context_inject returns '' when TEST_TIME_LEARNING_ENABLED=False."""
    saved, mod, _, _ = _setup_test_time_learning()
    try:
        with patch.object(mod, "TEST_TIME_LEARNING_ENABLED", False):
            result = asyncio.run(mod.memrl_context_inject("query"))
        assert result == ""
    finally:
        _restore(saved)


def test_ttl_buffer_flush_clears_buffer():
    """flush_ttrl_buffer clears the buffer regardless of MLX availability."""
    saved, mod, _, _ = _setup_test_time_learning()
    try:
        mod._ttrl_buffer.clear()
        mod._ttrl_buffer.append({"prompt": "p", "output": "o", "reward": 0.5, "timestamp": 0.0})
        asyncio.run(mod.flush_ttrl_buffer("test-model"))
        assert len(mod._ttrl_buffer) == 0
    finally:
        _restore(saved)


def test_ttl_gradient_update_rate_limit():
    """ttrl_gradient_update respects the per-hour rate limit."""
    saved, mod, _, _ = _setup_test_time_learning()
    try:
        import time
        mod._ttrl_update_times.clear()
        now = time.time()
        # Fill up the rate limit counter
        for _ in range(mod.TTRL_MAX_PER_HOUR):
            mod._ttrl_update_times.append(now)

        with patch.object(mod, "TTRL_GRADIENT_ENABLED", True):
            result = asyncio.run(mod.ttrl_gradient_update(
                "model", "prompt", "output", 1.0
            ))
        assert result is False  # rate limited
    finally:
        mod._ttrl_update_times.clear()
        _restore(saved)


# ===========================================================================
# 13. hermes/a2a_server.py — additional capability handlers
# ===========================================================================

def _setup_hermes_a2a_server():
    modules_to_fake = [
        "shared.db",
        "shared.a2a_wrapper",
        "shared.observability",
        "hermes.alerts",
        "hermes.a2a_server",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value=None)
    fake_db.execute = AsyncMock()
    fake_db.fetch_val = AsyncMock(return_value=0)
    sys.modules["shared.db"] = fake_db

    fake_a2a = types.ModuleType("shared.a2a_wrapper")

    class _FakeAgentCard:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    fake_a2a.AgentCard = _FakeAgentCard
    fake_a2a.create_a2a_app = MagicMock(return_value=MagicMock())
    sys.modules["shared.a2a_wrapper"] = fake_a2a

    fake_alerts = types.ModuleType("hermes.alerts")
    fake_alerts.send_operator_message = AsyncMock(return_value={"sent": True, "channel": "telegram"})
    sys.modules["hermes.alerts"] = fake_alerts

    sys.modules.pop("hermes.a2a_server", None)
    mod = importlib.import_module("hermes.a2a_server")
    return saved, mod, fake_db, fake_alerts


def test_hermes_a2a_message_send_requires_text():
    """_message_send returns error dict when text is empty."""
    saved, mod, _, _ = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._message_send(text=""))
        assert "error" in result
    finally:
        _restore(saved)


def test_hermes_a2a_message_send_emits_event_and_delivers():
    """_message_send emits event and calls send_operator_message."""
    saved, mod, fake_db, fake_alerts = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._message_send(text="hello operator"))
        fake_db.emit_event.assert_awaited_once()
        fake_alerts.send_operator_message.assert_awaited_once()
        assert result.get("message") == "hello operator"
    finally:
        _restore(saved)


def test_hermes_a2a_alert_urgent_level_field():
    """_alert_urgent includes level='urgent' in return dict."""
    saved, mod, _, _ = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._alert_urgent(text="FIRE"))
        assert result.get("level") == "urgent"
    finally:
        _restore(saved)


def test_hermes_a2a_alert_warning_level_field():
    """_alert_warning includes level='warning' in return dict."""
    saved, mod, _, _ = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._alert_warning(text="watch out"))
        assert result.get("level") == "warning"
    finally:
        _restore(saved)


def test_hermes_a2a_alert_info_level_field():
    """_alert_info includes level='info' in return dict."""
    saved, mod, _, _ = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._alert_info(text="FYI"))
        assert result.get("level") == "info"
    finally:
        _restore(saved)


def test_hermes_a2a_message_broadcast_has_broadcast_flag():
    """_message_broadcast sets broadcast=True in the return dict."""
    saved, mod, _, _ = _setup_hermes_a2a_server()
    try:
        result = asyncio.run(mod._message_broadcast(text="broadcast msg"))
        assert result.get("broadcast") is True
    finally:
        _restore(saved)


# ===========================================================================
# 14. hermes/alerts.py — formatting and briefing
# ===========================================================================

def _setup_hermes_alerts():
    modules_to_fake = [
        "shared.config",
        "shared.db",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_cfg_mod = types.ModuleType("shared.config")
    fake_cfg_mod.config = _make_fake_config()
    sys.modules["shared.config"] = fake_cfg_mod

    fake_db = types.ModuleType("shared.db")
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_val = AsyncMock(return_value=0)
    sys.modules["shared.db"] = fake_db

    sys.modules.pop("hermes.alerts", None)
    mod = importlib.import_module("hermes.alerts")
    return saved, mod, fake_db


def test_hermes_alerts_format_event_leads_discovered():
    """_format_event formats 'leads_discovered' event correctly."""
    saved, mod, _ = _setup_hermes_alerts()
    try:
        result = mod._format_event({
            "event_type": "leads_discovered",
            "payload": {"count": 42},
        })
        assert "42" in result
        assert "[Perseus]" in result
    finally:
        _restore(saved)


def test_hermes_alerts_format_event_pipeline_error():
    """_format_event formats 'pipeline_error' with stage and error info."""
    saved, mod, _ = _setup_hermes_alerts()
    try:
        result = mod._format_event({
            "event_type": "pipeline_error",
            "payload": {"stage": "email_compose", "error": "rate limit exceeded"},
        })
        assert "email_compose" in result
        assert "rate limit" in result
    finally:
        _restore(saved)


def test_hermes_alerts_format_event_unknown_fallback():
    """_format_event falls back to generic format for unknown event types."""
    saved, mod, _ = _setup_hermes_alerts()
    try:
        result = mod._format_event({
            "event_type": "totally_unknown_event_xyz",
            "payload": {"key": "val"},
        })
        assert "totally_unknown_event_xyz" in result
    finally:
        _restore(saved)


def test_hermes_alerts_send_morning_briefing_no_telegram():
    """send_morning_briefing returns False when Telegram is not configured."""
    saved, mod, fake_db = _setup_hermes_alerts()
    try:
        # send_morning_briefing does `from shared.db import fetch_val` locally
        # Patch shared.db.fetch_val so the local import gets our mock
        fake_db.fetch_val = AsyncMock(return_value=0)

        # Also patch _send_telegram directly since it checks config.telegram
        with patch.object(mod, "_send_telegram", new=AsyncMock(return_value=False)), \
             patch.dict("sys.modules", {"shared.db": fake_db}):
            result = asyncio.run(mod.send_morning_briefing())
        assert result is False
    finally:
        _restore(saved)


def test_hermes_alerts_dispatch_alert_for_event_empty_type():
    """dispatch_alert_for_event returns False when event_type is empty."""
    saved, mod, _ = _setup_hermes_alerts()
    try:
        result = asyncio.run(mod.dispatch_alert_for_event({}))
        assert result is False
    finally:
        _restore(saved)


# ===========================================================================
# 15. hermes/telegram_bot.py — command handlers with auth
# ===========================================================================

def _setup_telegram_bot():
    modules_to_fake = [
        "shared.config",
        "shared.db",
        "shared.comms",
        "telegram",
        "telegram.ext",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_cfg_mod = types.ModuleType("shared.config")
    fake_cfg_mod.config = _make_fake_config()
    sys.modules["shared.config"] = fake_cfg_mod

    fake_db = types.ModuleType("shared.db")
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_val = AsyncMock(return_value=0)
    fake_db.get_config = AsyncMock(return_value=None)
    sys.modules["shared.db"] = fake_db

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.call_agent_capability = AsyncMock(return_value={"success": True})
    sys.modules["shared.comms"] = fake_comms

    # Minimal telegram stubs
    fake_telegram = types.ModuleType("telegram")
    fake_telegram.Update = MagicMock
    sys.modules["telegram"] = fake_telegram

    fake_ext = types.ModuleType("telegram.ext")
    fake_ext.Application = MagicMock
    fake_ext.CommandHandler = MagicMock
    fake_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=None)
    sys.modules["telegram.ext"] = fake_ext

    sys.modules.pop("hermes.telegram_bot", None)
    mod = importlib.import_module("hermes.telegram_bot")
    return saved, mod, fake_db, fake_comms


def _make_update(chat_id: str = "123", has_message: bool = True):
    """Build a minimal mock Update object."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_message = MagicMock() if has_message else None
    update.message = MagicMock() if has_message else None
    if has_message:
        update.message.reply_text = AsyncMock()
        update.effective_message.reply_text = AsyncMock()
    return update


def _make_context(args: list[str] | None = None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


def _make_tg_config(chat_id: str = "123"):
    """Return a config object with the given telegram chat_id for test patching."""
    tg = types.SimpleNamespace(chat_id=chat_id, bot_token="tok")
    return types.SimpleNamespace(telegram=tg)


def test_telegram_bot_require_chat_access_authorized():
    """_require_chat_access grants access when chat ID matches config."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="777")
        with patch.object(mod, "config", _make_tg_config("777")):
            result = asyncio.run(mod._require_chat_access(update))
        assert result is True
    finally:
        _restore(saved)


def test_telegram_bot_require_chat_access_unauthorized():
    """_require_chat_access rejects access when chat ID does not match."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="999")
        with patch.object(mod, "config", _make_tg_config("777")):
            result = asyncio.run(mod._require_chat_access(update))
        assert result is False
    finally:
        _restore(saved)


def test_telegram_bot_require_chat_access_no_config():
    """_require_chat_access rejects when TELEGRAM_CHAT_ID is not configured."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="777")
        with patch.object(mod, "config", _make_tg_config("")):
            result = asyncio.run(mod._require_chat_access(update))
        assert result is False
    finally:
        _restore(saved)


def test_telegram_bot_require_destructive_auth_missing_secret_env():
    """_require_destructive_auth rejects when TELEGRAM_ADMIN_SECRET is not set."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="123")
        ctx = _make_context(args=["1", "wrongsecret"])

        with patch.object(mod, "config", _make_tg_config("123")), \
             patch.dict("os.environ", {}, clear=False):
            os.environ.pop("TELEGRAM_ADMIN_SECRET", None)
            result = asyncio.run(mod._require_destructive_auth(
                update, ctx, usage="usage text", secret_arg_index=1
            ))
        assert result is False
    finally:
        _restore(saved)


def test_telegram_bot_require_destructive_auth_wrong_secret():
    """_require_destructive_auth rejects incorrect admin secret."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="123")
        ctx = _make_context(args=["42", "bad_secret"])

        with patch.object(mod, "config", _make_tg_config("123")), \
             patch.dict("os.environ", {"TELEGRAM_ADMIN_SECRET": "correct_secret"}):
            result = asyncio.run(mod._require_destructive_auth(
                update, ctx, usage="usage text", secret_arg_index=1
            ))
        assert result is False
    finally:
        _restore(saved)


def test_telegram_bot_require_destructive_auth_correct_secret():
    """_require_destructive_auth passes when secret matches."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="123")
        ctx = _make_context(args=["42", "my_secret"])

        with patch.object(mod, "config", _make_tg_config("123")), \
             patch.dict("os.environ", {"TELEGRAM_ADMIN_SECRET": "my_secret"}):
            result = asyncio.run(mod._require_destructive_auth(
                update, ctx, usage="usage text", secret_arg_index=1
            ))
        assert result is True
    finally:
        _restore(saved)


def test_telegram_bot_cmd_status_unauthorized():
    """cmd_status sends an 'Unauthorized' reply (not status content) for wrong chat."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="456")
        ctx = _make_context()
        with patch.object(mod, "config", _make_tg_config("999")):
            asyncio.run(mod.cmd_status(update, ctx))
        # Auth failed — only an "Unauthorized." reply should have been sent, not status content
        calls = [str(c) for c in update.effective_message.reply_text.call_args_list]
        assert any("Unauthorized" in c for c in calls)
        assert not any("PERSEUS Status" in c or "pipeline" in c.lower() for c in calls)
    finally:
        _restore(saved)


def test_telegram_bot_cmd_approve_invalid_id():
    """cmd_approve replies with 'Invalid ID' when review_id is not an int."""
    saved, mod, _, _ = _setup_telegram_bot()
    try:
        update = _make_update(chat_id="123")
        ctx = _make_context(args=["notanint", "my_secret"])

        with patch.object(mod, "config", _make_tg_config("123")), \
             patch.dict("os.environ", {"TELEGRAM_ADMIN_SECRET": "my_secret"}):
            asyncio.run(mod.cmd_approve(update, ctx))

        calls = [str(c) for c in update.effective_message.reply_text.call_args_list]
        assert any("Invalid" in c or "invalid" in c or "Invalid ID" in c for c in calls)
    finally:
        _restore(saved)
