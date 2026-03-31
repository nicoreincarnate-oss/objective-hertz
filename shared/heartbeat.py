"""Heartbeat lifecycle for daemon health monitoring.

Each daemon emits periodic heartbeats to the session_health table.
Stale heartbeats (>5min) trigger Hermes alert and state -> PAUSED.

Feature flag: HEARTBEAT_LIFECYCLE_ENABLED

The session_health table (migration 025) has columns:
  session_id UUID, agent_id VARCHAR, metrics JSONB, created_at TIMESTAMPTZ
Heartbeat rows use metrics = {"type": "heartbeat", "state": "alive"}.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

logger = logging.getLogger("perseus.heartbeat")

HEARTBEAT_INTERVAL_SECONDS = 30
STALE_THRESHOLD_SECONDS = 300  # 5 minutes


def _heartbeat_enabled() -> bool:
    return os.environ.get("HEARTBEAT_LIFECYCLE_ENABLED", "").lower() in ("true", "1")


class HeartbeatEmitter:
    """Periodic heartbeat signal to session_health table.

    Usage:
        emitter = HeartbeatEmitter(daemon_name="titan")
        await emitter.start()   # Call on EXECUTING state entry
        ...
        await emitter.stop()    # Call on state exit

    Parameters:
        daemon_name: Identifier for this daemon (perseus, titan, hermes, clawdbot, conway)
        interval: Seconds between heartbeats (default: 30)
        on_stale: Optional async callback invoked when stale heartbeat detected
    """

    def __init__(
        self,
        daemon_name: str,
        interval: int = HEARTBEAT_INTERVAL_SECONDS,
        on_stale: Callable[..., Awaitable[None]] | None = None,
    ):
        self._daemon_name = daemon_name
        self._interval = interval
        self._on_stale = on_stale
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_beat: float = 0.0

    async def start(self) -> None:
        """Begin emitting heartbeats. Idempotent."""
        if not _heartbeat_enabled():
            logger.debug("Heartbeat disabled for %s (flag OFF)", self._daemon_name)
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Heartbeat started for %s (interval=%ds)", self._daemon_name, self._interval)

    async def stop(self) -> None:
        """Stop emitting heartbeats. Idempotent."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Heartbeat stopped for %s", self._daemon_name)

    async def _loop(self) -> None:
        """Main heartbeat loop: emit signal, check for stale peers."""
        while self._running:
            try:
                await self._emit()
                await self._check_stale_peers()
            except Exception as exc:
                logger.warning("Heartbeat error for %s: %s", self._daemon_name, exc)
            await asyncio.sleep(self._interval)

    async def _emit(self) -> None:
        """Write heartbeat to session_health table."""
        from shared import db

        self._last_beat = time.time()
        await db.execute(
            """INSERT INTO session_health (session_id, agent_id, metrics)
               VALUES (%s, %s, %s)""",
            (
                str(uuid.uuid4()),
                self._daemon_name,
                '{"type": "heartbeat", "state": "alive"}',
            ),
        )

    async def _check_stale_peers(self) -> None:
        """Detect peer daemons with stale heartbeats (>5min)."""
        from shared import db

        stale = await db.fetch_all(
            """SELECT agent_id, MAX(created_at) as last_beat
               FROM session_health
               WHERE agent_id != %s
                 AND metrics->>'type' = 'heartbeat'
               GROUP BY agent_id
               HAVING MAX(created_at) < NOW() - INTERVAL '%s seconds'""",
            (self._daemon_name, STALE_THRESHOLD_SECONDS),
        )

        for row in stale:
            logger.warning(
                "Stale heartbeat: %s last seen %s",
                row["agent_id"], row["last_beat"],
            )
            if self._on_stale:
                await self._on_stale(row["agent_id"], row["last_beat"])

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_beat(self) -> float:
        return self._last_beat


async def alert_stale_daemon(daemon_name: str, last_beat: datetime) -> None:
    """Default stale handler: send alert via Hermes A2A."""
    try:
        from shared.comms import send_a2a

        await send_a2a(
            target="hermes",
            capability="alert",
            payload={
                "type": "daemon_stale",
                "daemon": daemon_name,
                "last_heartbeat": str(last_beat),
                "threshold_seconds": STALE_THRESHOLD_SECONDS,
                "severity": "warning",
            },
        )
    except Exception as exc:
        logger.error("Failed to alert Hermes about stale %s: %s", daemon_name, exc)
