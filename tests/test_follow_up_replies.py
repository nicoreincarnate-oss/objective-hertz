"""Regression tests for reply processing safety."""

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
    fake_db.get_config = AsyncMock(return_value="cmp_123")

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(classify=AsyncMock(return_value="interested"))

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")
    fake_memory.format_rules_for_prompt = AsyncMock(return_value="")

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


def test_check_replies_marks_read_only_after_processing():
    follow_up = load_follow_up_module()
    events = []

    class FakeInstantlyClient:
        async def list_emails(self, **kwargs):
            return [{"id": "reply_1", "email": "hello@example.com", "body": "Yes", "subject": "Re: hi"}]

        async def close(self):
            events.append(("close",))

    async def fake_process(reply: dict):
        events.append(("process", reply["id"]))
        return True

    fake_tools = types.ModuleType("tools.instantly_client")
    fake_tools.InstantlyClient = FakeInstantlyClient

    with patch.dict(sys.modules, {"tools.instantly_client": fake_tools}):
        with patch.object(follow_up, "_process_reply", AsyncMock(side_effect=fake_process)):
            with patch.object(follow_up, "_mark_reply_read", AsyncMock(side_effect=lambda email_id: events.append(("mark_read", email_id)))):
                run(follow_up._check_replies())

    assert events == [("close",), ("process", "reply_1"), ("mark_read", "reply_1")]


def test_check_replies_still_processes_when_close_raises():
    follow_up = load_follow_up_module()

    class FakeInstantlyClient:
        async def list_emails(self, **kwargs):
            return [{"id": "reply_1", "email": "hello@example.com", "body": "Yes", "subject": "Re: hi"}]

        async def close(self):
            raise RuntimeError("close boom")

    fake_tools = types.ModuleType("tools.instantly_client")
    fake_tools.InstantlyClient = FakeInstantlyClient

    with patch.dict(sys.modules, {"tools.instantly_client": fake_tools}):
        with patch.object(follow_up, "_process_reply", AsyncMock(return_value=True)) as process_reply:
            with patch.object(follow_up, "_mark_reply_read", AsyncMock(return_value=None)):
                run(follow_up._check_replies())

    process_reply.assert_awaited_once()
