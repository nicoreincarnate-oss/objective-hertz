"""Integration tests for WakeupQueue + emit_event (requires Postgres).

These tests verify the full LISTEN/NOTIFY flow:
1. emit_event() inserts into events table
2. Trigger fires pg_notify('wakeup_events', ...)
3. WakeupQueue receives notification
4. Matching subscription creates wakeup_request
5. wait_for_wakeup() returns immediately

Run with: SKIP_DB_TESTS=0 PYTHONPATH=. pytest tests/shared/test_wakeup_integration.py -v
Requires: Docker Postgres running, migration 029 applied.
"""

import asyncio
import os
from unittest.mock import patch

import pytest

# Mark all tests as requiring DB
pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB_TESTS", "1") == "1",
    reason="DB integration tests disabled (set SKIP_DB_TESTS=0 to enable)",
)


@pytest.fixture
async def db_pool():
    """Initialize and tear down DB pool."""
    from shared.db import init_pool, close_pool
    await init_pool(min_size=1, max_size=3)
    yield
    await close_pool()


@pytest.fixture
async def wakeup_queue(db_pool):
    """Create and start a WakeupQueue, shut down after test."""
    with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "true"}):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        await queue.start()
        yield queue
        await queue.shutdown()


class TestFullFlow:
    @pytest.mark.asyncio
    async def test_emit_event_triggers_wakeup(self, wakeup_queue):
        """End-to-end: emit_event() -> NOTIFY -> wakeup_request created."""
        from shared.db import emit_event, fetch_all

        # Subscribe test agent to test_event
        await wakeup_queue.subscribe("test_agent", "test_wakeup_flow", priority=5)

        # Emit event (trigger fires NOTIFY)
        event_id = await emit_event("test_wakeup_flow", {"test": True})
        assert event_id > 0

        # Give LISTEN loop time to process
        await asyncio.sleep(0.5)

        # Verify wakeup_request was created
        rows = await fetch_all(
            """SELECT agent_id, event_type, status, source
               FROM wakeup_requests
               WHERE agent_id = 'test_agent' AND event_type = 'test_wakeup_flow'
               ORDER BY created_at DESC LIMIT 1"""
        )
        assert len(rows) >= 1
        assert rows[0]["source"] == "event"
        assert rows[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_wait_wakes_on_emit(self, wakeup_queue):
        """wait_for_wakeup() returns immediately when emit_event() fires."""
        from shared.db import emit_event

        await wakeup_queue.subscribe("flow_agent", "instant_wake", priority=5)

        async def emit_after_delay():
            await asyncio.sleep(0.2)
            await emit_event("instant_wake", {"trigger": "test"})

        asyncio.create_task(emit_after_delay())

        result = await wakeup_queue.wait_for_wakeup("flow_agent", timeout=5.0)
        assert result["woken_by"] == "event"
        assert "instant_wake" in result["reasons"]

    @pytest.mark.asyncio
    async def test_coalescing_increments_count(self, wakeup_queue):
        """Multiple events of same type within minute bucket are coalesced."""
        from shared.db import emit_event, fetch_one

        await wakeup_queue.subscribe("coal_agent", "coal_event", priority=3)

        # Emit 3 events rapidly
        for _ in range(3):
            await emit_event("coal_event", {})
            await asyncio.sleep(0.1)

        await asyncio.sleep(0.5)

        # Check coalesced count
        row = await fetch_one(
            """SELECT coalesced_count FROM wakeup_requests
               WHERE agent_id = 'coal_agent' AND event_type = 'coal_event'
               ORDER BY created_at DESC LIMIT 1"""
        )
        assert row is not None
        assert row["coalesced_count"] >= 2  # At least 2 coalesced

    @pytest.mark.asyncio
    async def test_shadow_mode_logs_only(self, db_pool):
        """Shadow mode creates requests with status='logged', never signals."""
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "shadow"}):
            from shared.wakeup_queue import WakeupQueue
            from shared.db import emit_event, fetch_one

            queue = WakeupQueue()
            await queue.start()
            try:
                await queue.subscribe("shadow_agent", "shadow_event", priority=5)
                await emit_event("shadow_event", {})
                await asyncio.sleep(0.5)

                row = await fetch_one(
                    """SELECT status FROM wakeup_requests
                       WHERE agent_id = 'shadow_agent' AND event_type = 'shadow_event'
                       ORDER BY created_at DESC LIMIT 1"""
                )
                assert row is not None
                assert row["status"] == "logged"

                # wait_for_wakeup should timeout (shadow mode never signals)
                result = await queue.wait_for_wakeup("shadow_agent", timeout=0.3)
                assert result["woken_by"] == "timeout"
            finally:
                await queue.shutdown()

    @pytest.mark.asyncio
    async def test_unmatched_event_no_wakeup(self, wakeup_queue):
        """Events that match no subscription create no wakeup requests."""
        from shared.db import emit_event, fetch_all

        initial_count = len(await fetch_all(
            "SELECT 1 FROM wakeup_requests WHERE agent_id = 'nobody'"
        ))

        await emit_event("totally_unsubscribed_event", {})
        await asyncio.sleep(0.5)

        final_count = len(await fetch_all(
            "SELECT 1 FROM wakeup_requests WHERE agent_id = 'nobody'"
        ))
        assert final_count == initial_count


class TestMaintenance:
    @pytest.mark.asyncio
    async def test_expire_stale(self, wakeup_queue):
        """expire_stale_requests() marks expired rows."""
        from shared.db import execute

        # Insert an already-expired request
        await execute(
            """INSERT INTO wakeup_requests
                   (agent_id, source, status, expires_at, idempotency_key)
               VALUES ('stale_agent', 'timer', 'pending',
                       NOW() - INTERVAL '1 hour',
                       'stale_test_' || gen_random_uuid()::text)"""
        )

        expired = await wakeup_queue.expire_stale_requests()
        assert expired >= 1

    @pytest.mark.asyncio
    async def test_cleanup_old(self, wakeup_queue):
        """cleanup_old_requests() deletes old dispatched rows."""
        from shared.db import execute

        await execute(
            """INSERT INTO wakeup_requests
                   (agent_id, source, status, dispatched_at, created_at,
                    idempotency_key)
               VALUES ('old_agent', 'event', 'dispatched',
                       NOW() - INTERVAL '48 hours',
                       NOW() - INTERVAL '48 hours',
                       'old_test_' || gen_random_uuid()::text)"""
        )

        cleaned = await wakeup_queue.cleanup_old_requests(older_than_hours=24)
        assert cleaned >= 1
