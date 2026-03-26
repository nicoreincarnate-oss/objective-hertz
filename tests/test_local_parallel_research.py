"""Behavioral test: follow_up _send_follow_ups includes email_queued leads — Cycle 21."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch


def _setup_fakes():
    modules_to_fake = [
        "shared.db", "shared.llm_client", "shared.pipeline_alerts",
        "titan.state_machine", "titan.memory", "titan.training",
        "titan.pipeline.follow_up",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock(return_value=1)
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value=None)
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"subject": "Follow up", "body": "Hi"}'),
        classify=AsyncMock(return_value="interested"),
    )

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_memory = types.ModuleType("titan.memory")
    fake_memory.get_relevant_learnings = AsyncMock(return_value="")
    fake_memory.format_rules_for_prompt = AsyncMock(return_value="")
    fake_memory.attribute_reply_cause = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()
    fake_training.collect_email_outcome = AsyncMock()

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.memory"] = fake_memory
    sys.modules["titan.training"] = fake_training
    sys.modules.pop("titan.pipeline.follow_up", None)

    fu_mod = importlib.import_module("titan.pipeline.follow_up")
    return saved, fu_mod


def _restore(saved):
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


def test_send_follow_ups_includes_email_queued_status():
    """_send_follow_ups query must include 'email_queued' in the status filter."""
    saved, fu_mod = _setup_fakes()
    try:
        queries = []

        async def capturing_fetch_all(query, params=None):
            queries.append(query)
            return []

        with patch.object(fu_mod, "fetch_all", capturing_fetch_all):
            asyncio.run(fu_mod._send_follow_ups())

        follow_up_queries = [q for q in queries if "follow_up_count" in q]
        assert len(follow_up_queries) >= 1
        assert "email_queued" in follow_up_queries[0], \
            f"Follow-up query must include 'email_queued': {follow_up_queries[0]}"
    finally:
        _restore(saved)
