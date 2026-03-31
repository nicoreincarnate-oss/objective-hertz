"""Tests for Recursion Guard (Phase 12: FP-03)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure unrelated flags are off
_CLEAN_ENV = {
    "AGENT_STATE_MACHINE_ENABLED": "",
    "ATOMIC_CHECKOUT_ENABLED": "",
    "SESSION_HEALTH_ENABLED": "",
}


def _make_agent():
    from shared.agent_base import AgentBase

    class _TestAgent(AgentBase):
        name = "depth_test"
        description = "test"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def health_check(self):
            return {}

    return _TestAgent()


def _mock_db():
    """Create a mock db module for patching at point-of-use."""
    mock = MagicMock()
    mock.fetch_one = AsyncMock(return_value=None)
    mock.fetch_all = AsyncMock(return_value=[])
    mock.execute = AsyncMock()
    mock.get_config = AsyncMock(return_value=None)
    mock.emit_event = AsyncMock(return_value=0)
    mock.close_pool = AsyncMock()
    mock.insert_task = AsyncMock(return_value=None)

    @asynccontextmanager
    async def mock_txn():
        yield mock

    mock.transaction = mock_txn
    return mock


# ---------------------------------------------------------------------------
# insert_task depth parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_zero_default():
    """New tasks inserted with depth=0 by default."""
    with patch("shared.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [None, {"id": 42}]  # dedupe check, insert
        with patch("shared.db.enrich_payload_with_context", side_effect=lambda p: p):
            from shared.db import insert_task

            result = await insert_task("test_type", {"key": "val"}, priority=5)
            insert_call = mock_fetch.call_args_list[1]
            query = insert_call[0][0]
            params = insert_call[0][1]
            assert "depth" in query
            assert params[3] == 0


@pytest.mark.asyncio
async def test_depth_propagation():
    """spawn_child_task sets depth = parent + 1."""
    with patch.dict(os.environ, _CLEAN_ENV, clear=False):
        db = _mock_db()
        call_count = 0
        original_fetch = db.fetch_one

        async def mock_fetch(query, params=()):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"depth": 2}  # parent depth lookup
            return {"id": 99}  # insert returning id

        db.fetch_one = mock_fetch
        db.insert_task = AsyncMock(return_value=99)
        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            result = await agent.spawn_child_task(
                parent_task_id=10,
                task_type="child_type",
                payload={"data": "value"},
            )
            assert result == 99
            # Verify depth=3 was passed (parent_depth 2 + 1)
            call_kwargs = db.insert_task.call_args
            assert call_kwargs[1].get("depth") == 3


@pytest.mark.asyncio
async def test_max_depth_rejection():
    """Task with depth > max is rejected and failed."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "RECURSION_GUARD_ENABLED": "true"}, clear=False):
        db = _mock_db()
        db.get_config = AsyncMock(return_value=5)
        db.fetch_one = AsyncMock(return_value={"depth": 10})

        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            with patch.object(agent, "fail_task", new_callable=AsyncMock) as mock_fail:
                result = await agent.claim_task(42)
                assert result is False
                mock_fail.assert_called_once()
                assert "recursion_guard" in mock_fail.call_args[0][1]


@pytest.mark.asyncio
async def test_configurable_max_depth():
    """system_config max_task_depth overrides default 5."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "RECURSION_GUARD_ENABLED": "true"}, clear=False):
        db = _mock_db()
        db.get_config = AsyncMock(return_value=3)
        db.fetch_one = AsyncMock(return_value={"depth": 4})

        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            with patch.object(agent, "fail_task", new_callable=AsyncMock):
                result = await agent.claim_task(42)
                assert result is False


@pytest.mark.asyncio
async def test_feature_flag_off_skips_check():
    """Depth check skipped when flag is off."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "RECURSION_GUARD_ENABLED": ""}, clear=False):
        db = _mock_db()
        db.fetch_one = AsyncMock(return_value={"id": 42})

        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            with patch("shared.agent_base.record_task_claimed"):
                result = await agent.claim_task(42)
                assert result is True
