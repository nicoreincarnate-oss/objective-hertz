"""Behavioral tests for follow-up truthfulness — Cycle 21.

Tests that:
1. _compose_and_queue_follow_up does NOT transition lead to 'followed_up'
2. Reply lookup normalizes email case (LOWER comparison)
"""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch


def _setup_fakes():
    """Install fake modules, return (saved_originals, follow_up_module)."""
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
    fake_db.transaction = None

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(
        generate=AsyncMock(return_value='{"subject": "Follow up", "body": "Just checking in"}'),
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
    return saved, fu_mod, fake_state, fake_db


def _restore(saved):
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


def test_follow_up_does_not_transition_to_followed_up():
    """_compose_and_queue_follow_up must NOT call transition_lead('followed_up')."""
    saved, fu_mod, fake_state, fake_db = _setup_fakes()
    try:
        fake_state.transition_lead = AsyncMock()
        fake_db.fetch_one = AsyncMock(return_value={"id": 99})
        fake_db.execute = AsyncMock()

        lead = {"id": 1, "business_name": "TestCo", "email": "t@co.com",
                "follow_up_count": 0, "language": "en"}
        asyncio.run(fu_mod._compose_and_queue_follow_up(lead))

        for call in fake_state.transition_lead.await_args_list:
            assert call.args[1] != "followed_up", \
                f"transition_lead should not be called with 'followed_up', got: {call}"
    finally:
        _restore(saved)


def test_reply_lookup_is_case_insensitive():
    """_process_reply must use case-insensitive email lookup."""
    saved, fu_mod, fake_state, fake_db = _setup_fakes()
    try:
        queries = []

        async def capturing_fetch_one(query, params=None):
            queries.append((query, params))
            if "clients" in query:
                return {"id": 1, "status": "email_sent"}
            if "email_sequences" in query:
                return {"id": 10}
            return None

        with patch.object(fu_mod, "fetch_one", capturing_fetch_one):
            reply = {"lead": "Test@Example.COM", "body": "I'm interested!", "subject": "Re: Website"}
            asyncio.run(fu_mod._process_reply(reply))

        client_queries = [q for q, p in queries if "clients" in q]
        assert len(client_queries) >= 1
        assert "LOWER" in client_queries[0], f"Email lookup must use LOWER(): {client_queries[0]}"
    finally:
        _restore(saved)
