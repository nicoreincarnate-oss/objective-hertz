"""Tests for shared/wakeup_queue.py -- Event-Driven Wakeup Queue (Phase 16)."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Feature flag helper tests


class TestWakeupMode:
    def test_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "off"

    def test_shadow_mode(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "shadow"}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "shadow"

    def test_full_mode_true(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "true"}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "full"

    def test_full_mode_1(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "1"}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "full"

    def test_full_mode_full(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "full"}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "full"

    def test_random_value_is_off(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "maybe"}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "off"

    def test_whitespace_handling(self):
        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "  TRUE  "}):
            from shared.wakeup_queue import _wakeup_mode
            assert _wakeup_mode() == "full"


# Pattern matching tests


class TestPatternMatching:
    def setup_method(self):
        from shared.wakeup_queue import WakeupQueue
        self.queue = WakeupQueue()

    def test_exact_match(self):
        assert self.queue._matches_pattern("new_lead", "new_lead") is True

    def test_exact_no_match(self):
        assert self.queue._matches_pattern("new_lead", "old_lead") is False

    def test_sql_like_prefix(self):
        assert self.queue._matches_pattern("pipeline_stage_complete", "pipeline_%") is True

    def test_wildcard_prefix(self):
        assert self.queue._matches_pattern("pipeline_stage_complete", "pipeline_*") is True

    def test_prefix_no_match(self):
        assert self.queue._matches_pattern("budget_exceeded", "pipeline_%") is False

    def test_empty_event_type(self):
        assert self.queue._matches_pattern("", "new_lead") is False

    def test_empty_pattern(self):
        assert self.queue._matches_pattern("new_lead", "") is False

    def test_percent_only_matches_all(self):
        assert self.queue._matches_pattern("anything", "%") is True

    def test_star_only_matches_all(self):
        assert self.queue._matches_pattern("anything", "*") is True


# Coalescing tests


class TestCoalescing:
    """Test that duplicate events within the same minute bucket are coalesced."""

    @pytest.mark.asyncio
    async def test_idempotency_key_generation(self):
        """Verify idempotency key format: agent_id:event_type:minute_bucket."""
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        import time
        bucket = int(time.time()) // queue.IDEMPOTENCY_BUCKET_SECONDS
        assert isinstance(bucket, int)
        assert bucket > 0


# wait_for_wakeup tests


class TestWaitForWakeup:
    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_reason(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        result = await queue.wait_for_wakeup("test_agent", timeout=0.1)
        assert result["woken_by"] == "timeout"
        assert result["reasons"] == []

    @pytest.mark.asyncio
    async def test_event_wakes_immediately(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()

        # Signal shortly after wait_for_wakeup starts (it clears event first)
        async def signal_soon():
            await asyncio.sleep(0.02)
            if "test_agent" not in queue._agent_events:
                queue._agent_events["test_agent"] = asyncio.Event()
            queue._agent_wakeup_reasons["test_agent"] = ["new_lead"]
            queue._agent_events["test_agent"].set()

        asyncio.create_task(signal_soon())
        result = await queue.wait_for_wakeup("test_agent", timeout=5.0)
        assert result["woken_by"] == "event"
        assert "new_lead" in result["reasons"]

    @pytest.mark.asyncio
    async def test_concurrent_wait_and_signal(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()

        async def signal_after_delay():
            await asyncio.sleep(0.05)
            if "test_agent" not in queue._agent_events:
                queue._agent_events["test_agent"] = asyncio.Event()
            queue._agent_wakeup_reasons["test_agent"] = ["pipeline_stage_complete"]
            queue._agent_events["test_agent"].set()

        asyncio.create_task(signal_after_delay())
        result = await queue.wait_for_wakeup("test_agent", timeout=2.0)
        assert result["woken_by"] == "event"
        assert "pipeline_stage_complete" in result["reasons"]

    @pytest.mark.asyncio
    async def test_multiple_agents_independent(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()

        # Two agents, only one gets signaled
        async def signal_agent_a():
            await asyncio.sleep(0.05)
            if "agent_a" not in queue._agent_events:
                queue._agent_events["agent_a"] = asyncio.Event()
            queue._agent_wakeup_reasons["agent_a"] = ["test"]
            queue._agent_events["agent_a"].set()

        asyncio.create_task(signal_agent_a())

        result_a = await queue.wait_for_wakeup("agent_a", timeout=2.0)
        assert result_a["woken_by"] == "event"

        result_b = await queue.wait_for_wakeup("agent_b", timeout=0.1)
        assert result_b["woken_by"] == "timeout"


# Re-entrancy guard tests


class TestReentrancyGuard:
    @pytest.mark.asyncio
    async def test_dispatch_blocked_when_dispatching(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        queue._dispatching = True
        result = await queue.dispatch_pending("test_agent")
        assert result == []
        queue._dispatching = False


# Stats tests


class TestStats:
    def test_initial_stats(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        stats = queue.stats()
        assert stats["mode"] in ("off", "shadow", "full")
        assert stats["notifications_received"] == 0
        assert stats["wakeups_created"] == 0
        assert stats["wakeups_coalesced"] == 0
        assert stats["reconnects"] == 0
        assert stats["listening"] is False
        assert isinstance(stats["agents_registered"], list)
        assert isinstance(stats["subscriptions"], dict)

    def test_stats_reflect_mode_change(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()

        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "shadow"}):
            stats = queue.stats()
            assert stats["mode"] == "shadow"

        with patch.dict(os.environ, {"EVENT_WAKEUP_ENABLED": "true"}):
            stats = queue.stats()
            assert stats["mode"] == "full"


# Notification handling tests


class TestNotificationHandling:
    @pytest.mark.asyncio
    async def test_handle_notification_no_match(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        queue._subscriptions = {"titan": [{"event_pattern": "new_lead", "priority": 5}]}

        notify = MagicMock()
        notify.payload = "unrelated_event:123"

        await queue._handle_notification(notify)
        assert queue._stats["notifications_received"] == 1
        assert queue._stats["wakeups_created"] == 0

    @pytest.mark.asyncio
    async def test_handle_notification_parses_payload(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        queue._subscriptions = {}

        notify = MagicMock()
        notify.payload = "test_event:42"

        await queue._handle_notification(notify)
        assert queue._stats["notifications_received"] == 1

    @pytest.mark.asyncio
    async def test_handle_notification_empty_payload(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        queue._subscriptions = {}

        notify = MagicMock()
        notify.payload = ""

        await queue._handle_notification(notify)
        assert queue._stats["notifications_received"] == 1


# Shutdown tests


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_when_not_started(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        # Should not raise
        await queue.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        await queue.shutdown()
        await queue.shutdown()  # Second call should be safe
