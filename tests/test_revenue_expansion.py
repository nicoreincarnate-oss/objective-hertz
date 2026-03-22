"""Tests for the revenue-driven expansion gate."""

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.run(coro)


def load_expansion_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.fetch_val = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.set_config = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(generate=AsyncMock())

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="Reply rate drives revenue")

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.request_task_result = AsyncMock()

    fake_skills = types.ModuleType("shared.skill_loader")
    fake_skills.find_skill = lambda name: Path(f"/fake/{name}/SKILL.md")
    fake_skills.list_installed_skills = lambda: [{"name": "apify-lead-generation"}]

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg_types = types.ModuleType("psycopg.types")
    fake_json = types.ModuleType("psycopg.types.json")

    class FakeJsonb:
        def __init__(self, value):
            self.value = value

    fake_json.Jsonb = FakeJsonb
    fake_psycopg.types = fake_psycopg_types
    fake_psycopg_types.json = fake_json

    sys.modules.pop("titan.expansion", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["titan.memory"] = fake_memory
    sys.modules["shared.comms"] = fake_comms
    sys.modules["shared.skill_loader"] = fake_skills
    sys.modules["psycopg"] = fake_psycopg
    sys.modules["psycopg.types"] = fake_psycopg_types
    sys.modules["psycopg.types.json"] = fake_json
    return importlib.import_module("titan.expansion")


def test_detect_revenue_bottlenecks_flags_low_reply_rate():
    expansion = load_expansion_module()

    bottlenecks = expansion._detect_revenue_bottlenecks(
        {
            "emails_sent_14d": 250,
            "reply_rate_14d": 0.9,
            "replies_14d": 5,
            "interest_rate_14d": 5.0,
            "interested_open": 0,
            "proposal_backlog": 0,
            "closed_uninvoiced": 0,
            "discovered_missing_email_7d": 0,
        }
    )

    assert any(b["bottleneck"] == "low_reply_rate" for b in bottlenecks)


def test_gate_candidate_rejects_low_roi():
    expansion = load_expansion_module()

    allowed, reason = expansion._gate_candidate(
        {
            "capability_type": "tool",
            "expected_monthly_revenue_gain": 50,
            "expected_monthly_cost": 40,
            "expected_roi": 1.2,
        },
        budget=expansion.Decimal("50"),
        min_roi=1.5,
    )

    assert allowed is False
    assert "below required" in reason


def test_review_revenue_expansion_starts_shadow_for_installed_discovery_skill():
    expansion = load_expansion_module()

    metrics_row = {
        "emails_sent_14d": 200,
        "replies_14d": 1,
        "interested_replies_14d": 0,
    }

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "FROM outreach_metrics" in query:
            return metrics_row
        if "FROM revenue_expansion_opportunities" in query and "status = 'shadow'" in query:
            return None
        if "WHERE capability_name = %s" in query:
            return None
        if "INSERT INTO revenue_expansion_opportunities" in query:
            return {"id": 41}
        return None

    async def fake_fetch_val(query: str, params: tuple = ()):
        if "WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating')" in query:
            return 0
        if "WHERE status IN ('interested', 'demo_built')" in query:
            return 0
        if "WHERE status = 'closed'" in query:
            return 0
        if "email = ''" in query:
            return 0
        if "FROM deals" in query:
            return 0
        return 0

    async def fake_get_config(key: str, default=None):
        values = {
            "expansion_enabled": True,
            "expansion_monthly_budget": 50,
            "expansion_min_expected_roi": 1.5,
            "expansion_shadow_percent": 10,
            "active_shadow_discovery_skill": "",
            "active_shadow_opportunity_id": 0,
        }
        return values.get(key, default)

    expansion.llm.generate = AsyncMock(
        return_value="""[
            {
              "title": "Shadow apify discovery",
              "stage": "lead_discovery",
              "bottleneck": "low_reply_rate",
              "capability_type": "skill",
              "capability_name": "apify-lead-generation",
              "target_metric": "reply_rate_14d",
              "success_metric": "reply rate improves versus baseline",
              "expected_monthly_revenue_gain": 500,
              "expected_monthly_cost": 10,
              "expected_roi": 50,
              "reasoning": "Better lead quality should raise replies",
              "smallest_step": "Run apify on 10% of discovery traffic",
              "rollback_condition": "Reject if no lift after 10 leads"
            }
        ]"""
    )

    with patch.object(expansion, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
        with patch.object(expansion, "fetch_val", AsyncMock(side_effect=fake_fetch_val)):
            with patch.object(expansion, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch.object(expansion, "set_config", AsyncMock()) as set_config:
                    created = run(expansion.review_revenue_expansion())

    assert created == [41]
    set_config.assert_any_await("active_shadow_discovery_skill", "apify-lead-generation")
    set_config.assert_any_await("active_shadow_opportunity_id", 41)


def test_review_revenue_expansion_routes_tool_builds_through_clawdbot_builder():
    expansion = load_expansion_module()
    expansion.list_installed_skills = lambda: [
        {"name": "codex-collab"},
        {"name": "apify-lead-generation"},
    ]

    metrics_row = {
        "emails_sent_14d": 200,
        "replies_14d": 1,
        "interested_replies_14d": 0,
    }

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "FROM outreach_metrics" in query:
            return metrics_row
        if "FROM revenue_expansion_opportunities" in query and "status = 'shadow'" in query:
            return None
        if "WHERE capability_name = %s" in query:
            return None
        if "INSERT INTO revenue_expansion_opportunities" in query:
            return {"id": 55}
        return None

    async def fake_fetch_val(query: str, params: tuple = ()):
        return 0

    async def fake_get_config(key: str, default=None):
        values = {
            "expansion_enabled": True,
            "expansion_monthly_budget": 50,
            "expansion_min_expected_roi": 1.5,
            "expansion_shadow_percent": 10,
            "active_shadow_discovery_skill": "",
            "active_shadow_opportunity_id": 0,
        }
        return values.get(key, default)

    expansion.llm.generate = AsyncMock(
        return_value="""[
            {
              "title": "Build invoice recovery tool",
              "stage": "invoice",
              "bottleneck": "invoice_delay",
              "capability_type": "tool",
              "capability_name": "invoice-recovery-tool",
              "target_metric": "closed_uninvoiced",
              "success_metric": "fewer closed deals waiting for payment",
              "expected_monthly_revenue_gain": 300,
              "expected_monthly_cost": 10,
              "expected_roi": 30,
              "reasoning": "Reduce payment delay for closed deals",
              "smallest_step": "Add a payment follow-up automation",
              "rollback_condition": "Remove if no reduction in invoice delay"
            }
        ]"""
    )
    expansion.request_task_result = AsyncMock(return_value={"ok": True, "result": {"result": '{"writer":"codex"}'}})

    with patch.object(expansion, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
        with patch.object(expansion, "fetch_val", AsyncMock(side_effect=fake_fetch_val)):
            with patch.object(expansion, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch.object(expansion, "execute", AsyncMock()) as execute:
                    created = run(expansion.review_revenue_expansion())

    assert created == [55]
    expansion.request_task_result.assert_awaited_once()
    request_payload = expansion.request_task_result.await_args.kwargs["payload"]
    assert request_payload["skill_name"] == "codex-collab"
    assert execute.await_count >= 1


def test_runtime_wiring_exists_for_revenue_expansion():
    scheduler_code = (ROOT / "perseus" / "scheduler.py").read_text()
    daemon_code = (ROOT / "titan" / "daemon.py").read_text()
    discovery_code = (ROOT / "titan" / "pipeline" / "lead_discovery.py").read_text()
    readme_code = (ROOT / "README.md").read_text()

    assert 'Schedule("revenue_expansion_review"' in scheduler_code
    assert '"revenue_expansion_review": review_revenue_expansion' in daemon_code
    assert "active_shadow_discovery_skill" in discovery_code
    assert "preferred_discovery_skill" in discovery_code
    assert "ClawdBot using installed OpenClaw/Codex/Claude-compatible builder skills" in readme_code
