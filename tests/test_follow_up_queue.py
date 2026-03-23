"""Regression tests for follow-up queue persistence."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


def load_follow_up_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.emit_event = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"subject":"Hi","body":"Follow up"}'),
        classify=AsyncMock(return_value="interested"),
    )

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="short emails work")
    fake_memory.format_rules_for_prompt = AsyncMock(return_value="")
    fake_memory.attribute_reply_cause = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_email_outcome = AsyncMock()
    fake_training.collect_training_example = AsyncMock()

    sys.modules.pop("titan.pipeline.follow_up", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state_machine
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    return importlib.import_module("titan.pipeline.follow_up")


def test_compose_and_queue_follow_up_does_not_advance_without_inserted_sequence():
    follow_up = load_follow_up_module()
    lead = {
        "id": 42,
        "business_name": "Acme Plumbing",
        "follow_up_count": 1,
        "language": "en",
    }

    with patch.object(follow_up, "fetch_one", AsyncMock(return_value=None)):
        with patch.object(follow_up, "execute", AsyncMock()) as execute:
            with patch.object(follow_up, "transition_lead", AsyncMock()) as transition:
                with patch.object(follow_up, "emit_pipeline_error", AsyncMock()) as emit_error:
                    run(follow_up._compose_and_queue_follow_up(lead))

    execute.assert_not_awaited()
    transition.assert_not_awaited()
    emit_error.assert_awaited_once()
