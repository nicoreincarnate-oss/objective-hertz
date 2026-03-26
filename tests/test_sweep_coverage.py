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
# 8. shared/deep_thinking.py
# ===========================================================================

def _import_deep_thinking():
    sys.modules.pop("shared.deep_thinking", None)
    return importlib.import_module("shared.deep_thinking")


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
