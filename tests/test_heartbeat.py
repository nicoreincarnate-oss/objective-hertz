"""Tests for heartbeat lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_heartbeat_emitter_start_stop():
    """HeartbeatEmitter starts and stops cleanly."""
    from shared.heartbeat import HeartbeatEmitter

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="test", interval=1)
        await emitter.start()
        assert emitter.is_running
        await asyncio.sleep(0.1)
        await emitter.stop()
        assert not emitter.is_running


@pytest.mark.asyncio
async def test_heartbeat_emitter_noop_when_disabled():
    """HeartbeatEmitter does nothing when flag is OFF."""
    from shared.heartbeat import HeartbeatEmitter

    with patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "false"}):
        emitter = HeartbeatEmitter(daemon_name="test")
        await emitter.start()
        assert not emitter.is_running  # Should not start


@pytest.mark.asyncio
async def test_heartbeat_emitter_idempotent_start():
    """Multiple start() calls don't create multiple tasks."""
    from shared.heartbeat import HeartbeatEmitter

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="test", interval=60)
        await emitter.start()
        task1 = emitter._task
        await emitter.start()  # Second call
        task2 = emitter._task
        assert task1 is task2  # Same task, not a new one
        await emitter.stop()


@pytest.mark.asyncio
async def test_heartbeat_emitter_idempotent_stop():
    """Multiple stop() calls don't raise errors."""
    from shared.heartbeat import HeartbeatEmitter

    with patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "false"}):
        emitter = HeartbeatEmitter(daemon_name="test")
        await emitter.stop()  # Should not raise
        await emitter.stop()  # Should not raise


@pytest.mark.asyncio
async def test_stale_detection_triggers_callback():
    """Stale peer heartbeat triggers on_stale callback."""
    from datetime import datetime, timedelta

    from shared.heartbeat import HeartbeatEmitter

    callback = AsyncMock()
    stale_time = datetime.now(UTC) - timedelta(minutes=10)

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[
             {"agent_id": "titan", "last_beat": stale_time}
         ]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="perseus", interval=1, on_stale=callback)
        await emitter.start()
        await asyncio.sleep(1.5)  # Wait for one heartbeat cycle
        await emitter.stop()

        assert callback.called, "Stale callback should fire for stale peer"


@pytest.mark.asyncio
async def test_heartbeat_emit_writes_to_db():
    """Heartbeat _emit() writes to session_health with parameterized SQL."""
    from shared.heartbeat import HeartbeatEmitter

    mock_execute = AsyncMock()

    with patch("shared.db.execute", mock_execute), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="titan", interval=1)
        await emitter.start()
        await asyncio.sleep(1.5)
        await emitter.stop()

        assert mock_execute.called, "Heartbeat should write to DB"
        call_args = mock_execute.call_args_list[0]
        sql = call_args[0][0]
        assert "INSERT INTO session_health" in sql
        assert "%s" in sql  # parameterized
        assert "f\"" not in sql  # no f-strings


@pytest.mark.asyncio
async def test_heartbeat_updates_last_beat():
    """Heartbeat updates last_beat timestamp after emit."""
    from shared.heartbeat import HeartbeatEmitter

    with patch("shared.db.execute", new_callable=AsyncMock), \
         patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]), \
         patch.dict("os.environ", {"HEARTBEAT_LIFECYCLE_ENABLED": "true"}):
        emitter = HeartbeatEmitter(daemon_name="test", interval=1)
        assert emitter.last_beat == 0.0
        await emitter.start()
        await asyncio.sleep(1.5)
        await emitter.stop()
        assert emitter.last_beat > 0.0, "last_beat should be updated after emit"
