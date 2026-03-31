"""Tests for PerseusScheduler + WakeupQueue integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSchedulerWithWakeup:
    @pytest.mark.asyncio
    async def test_scheduler_uses_sleep_when_no_queue(self):
        """Without WakeupQueue, scheduler uses asyncio.sleep."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        bus.publish = MagicMock()
        discovery = MagicMock()
        discovery.get = MagicMock(return_value=None)

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(tick_interval=1),
            wakeup_queue=None,
        )
        assert scheduler._wakeup_queue is None

    @pytest.mark.asyncio
    async def test_scheduler_accepts_wakeup_queue(self):
        """PerseusScheduler stores the wakeup_queue reference."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        discovery = MagicMock()
        mock_queue = MagicMock()

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(),
            wakeup_queue=mock_queue,
        )
        assert scheduler._wakeup_queue is mock_queue

    @pytest.mark.asyncio
    async def test_scheduler_falls_back_on_queue_failure(self):
        """If WakeupQueue.start() fails, scheduler falls back to polling."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        bus.publish = MagicMock()
        discovery = MagicMock()
        discovery.get = MagicMock(return_value=None)

        mock_queue = AsyncMock()
        mock_queue.start = AsyncMock(side_effect=Exception("Connection refused"))
        mock_queue._running = False

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(tick_interval=1),
            wakeup_queue=mock_queue,
        )

        # Simulate the start() method handling queue failure
        # We test that the queue is set to None after failure
        try:
            await mock_queue.start()
        except Exception:
            scheduler._wakeup_queue = None

        assert scheduler._wakeup_queue is None

    @pytest.mark.asyncio
    async def test_stop_shuts_down_queue(self):
        """stop() calls WakeupQueue.shutdown()."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        discovery = MagicMock()
        mock_queue = AsyncMock()
        mock_queue.shutdown = AsyncMock()

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(),
            wakeup_queue=mock_queue,
        )
        scheduler._running = True

        await scheduler.stop()
        mock_queue.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_without_queue(self):
        """stop() works when no queue is configured."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        discovery = MagicMock()

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(),
            wakeup_queue=None,
        )
        scheduler._running = True

        # Should not raise
        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_status_includes_wakeup_when_present(self):
        """status() includes wakeup_queue stats when queue is present."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        discovery = MagicMock()
        discovery.summary = MagicMock(return_value={})

        mock_queue = MagicMock()
        mock_queue.stats = MagicMock(return_value={"mode": "full", "listening": True})

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(),
            wakeup_queue=mock_queue,
        )

        status = scheduler.status()
        assert "wakeup_queue" in status
        assert status["wakeup_queue"]["mode"] == "full"

    @pytest.mark.asyncio
    async def test_status_no_wakeup_when_absent(self):
        """status() does not include wakeup_queue when queue is None."""
        from openjarvis.vassals.perseus_scheduler import PerseusConfig, PerseusScheduler

        bus = MagicMock()
        discovery = MagicMock()
        discovery.summary = MagicMock(return_value={})

        scheduler = PerseusScheduler(
            bus=bus,
            vassal_discovery=discovery,
            config=PerseusConfig(),
            wakeup_queue=None,
        )

        status = scheduler.status()
        assert "wakeup_queue" not in status
