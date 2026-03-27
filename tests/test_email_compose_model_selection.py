"""Regression tests for money-first email composition routing."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock


def run(coro):
    return asyncio.run(coro)


def load_email_compose_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock(return_value={"id": 1})
    fake_db.execute = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"subject":"Hi","body":"Body"}'))

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_skills = types.ModuleType("shared.skill_loader")
    fake_skills.find_skill = lambda name: None
    fake_skills.execute_skill = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")
    fake_memory.compute_prompt_version = lambda soul_copy, rules, ab_variation="": "test_hash_0000"

    sys.modules.pop("titan.pipeline.email_compose", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["shared.skill_loader"] = fake_skills
    sys.modules["titan.state_machine"] = fake_state_machine
    sys.modules["titan.memory"] = fake_memory
    return importlib.import_module("titan.pipeline.email_compose")


def test_compose_one_uses_local_for_lower_score_leads():
    module = load_email_compose_module()
    lead = {
        "id": 1,
        "business_name": "Acme",
        "lead_score": 55,
        "language": "en",
    }

    run(module._compose_one(lead, "guidelines", "tips"))

    assert module.llm.generate.await_args.kwargs["model"] == "fast"


def test_compose_one_uses_smart_for_high_score_leads():
    module = load_email_compose_module()
    lead = {
        "id": 1,
        "business_name": "Acme",
        "lead_score": 88,
        "language": "en",
    }

    run(module._compose_one(lead, "guidelines", "tips"))

    assert module.llm.generate.await_args.kwargs["model"] == "smart"
