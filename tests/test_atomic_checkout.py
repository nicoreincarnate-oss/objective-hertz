"""Tests for Atomic Task Checkout (Phase 12: FP-02)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure state machine and other flags are off during these tests
_CLEAN_ENV = {
    "AGENT_STATE_MACHINE_ENABLED": "",
    "RECURSION_GUARD_ENABLED": "",
    "SESSION_HEALTH_ENABLED": "",
}


def _make_agent():
    """Create a minimal test agent."""
    from shared.agent_base import AgentBase

    class _TestAgent(AgentBase):
        name = "atomic_test"
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

    @asynccontextmanager
    async def mock_txn():
        yield mock

    mock.transaction = mock_txn
    return mock


@pytest.mark.asyncio
async def test_feature_flag_off_uses_original_query():
    """Existing behavior unchanged when flag is off."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "ATOMIC_CHECKOUT_ENABLED": ""}, clear=False):
        db = _mock_db()
        db.fetch_one = AsyncMock(return_value={"id": 1})
        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            with patch("shared.agent_base.record_task_claimed"):
                result = await agent.claim_task(1)
                assert result is True


@pytest.mark.asyncio
async def test_savepoint_rollback_on_stale_task():
    """Claiming already-running task returns False with SAVEPOINT rollback."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "ATOMIC_CHECKOUT_ENABLED": "true"}, clear=False):
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        db = _mock_db()

        @asynccontextmanager
        async def mock_txn():
            yield mock_conn

        db.transaction = mock_txn

        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            result = await agent.claim_task(42)
            assert result is False
            calls = [str(c) for c in mock_conn.execute.call_args_list]
            assert any("ROLLBACK TO SAVEPOINT" in c for c in calls)


@pytest.mark.asyncio
async def test_claim_within_transaction():
    """Successful claim commits properly with RELEASE SAVEPOINT."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "ATOMIC_CHECKOUT_ENABLED": "true"}, clear=False):
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"id": 1})
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        db = _mock_db()

        @asynccontextmanager
        async def mock_txn():
            yield mock_conn

        db.transaction = mock_txn

        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            with patch("shared.agent_base.record_task_claimed"):
                result = await agent.claim_task(1)
                assert result is True
                calls = [str(c) for c in mock_conn.execute.call_args_list]
                assert any("RELEASE SAVEPOINT" in c for c in calls)


@pytest.mark.asyncio
async def test_skip_locked_in_query():
    """get_pending_tasks uses FOR UPDATE SKIP LOCKED when flag is on."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "ATOMIC_CHECKOUT_ENABLED": "true"}, clear=False):
        db = _mock_db()
        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            await agent.get_pending_tasks()
            query = db.fetch_all.call_args[0][0]
            assert "FOR UPDATE SKIP LOCKED" in query


@pytest.mark.asyncio
async def test_no_skip_locked_when_off():
    """get_pending_tasks uses normal SELECT when flag is off."""
    with patch.dict(os.environ, {**_CLEAN_ENV, "ATOMIC_CHECKOUT_ENABLED": ""}, clear=False):
        db = _mock_db()
        with patch("shared.agent_base.db", db):
            agent = _make_agent()
            await agent.get_pending_tasks()
            query = db.fetch_all.call_args[0][0]
            assert "FOR UPDATE SKIP LOCKED" not in query
