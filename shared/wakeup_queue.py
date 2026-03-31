"""
Event-driven wakeup queue using Postgres LISTEN/NOTIFY.

Replaces fixed-interval polling with near-instant event-driven dispatch.
Feature flag: EVENT_WAKEUP_ENABLED (shadow | true | false)

Architecture:
- Dedicated Postgres connection (outside pool) for LISTEN
- Subscriptions stored in wakeup_subscriptions table
- Incoming NOTIFY -> match against subscriptions -> create wakeup_request
- Agents call wait_for_wakeup() which blocks until wakeup or timeout
- Duplicate coalescing via idempotency_key

This module NEVER imports from perseus/, titan/, hermes/, or clawdbot/.
It is a shared infrastructure primitive.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from shared.config import config

logger = logging.getLogger("perseus.wakeup_queue")

# -- Feature flag --------------------------------------------------------


def _wakeup_mode() -> str:
    """Return wakeup mode: 'off', 'shadow', or 'full'."""
    raw = os.environ.get("EVENT_WAKEUP_ENABLED", "").lower().strip()
    if raw in ("true", "1", "full"):
        return "full"
    if raw == "shadow":
        return "shadow"
    return "off"


class WakeupQueue:
    """Event-driven agent wakeup using Postgres LISTEN/NOTIFY.

    Lifecycle:
        queue = WakeupQueue()
        await queue.start()      # Spawns LISTEN task
        ...
        wakeup = await queue.wait_for_wakeup("perseus", timeout=60)
        ...
        await queue.shutdown()   # Closes dedicated connection
    """

    CHANNEL = "wakeup_events"
    IDEMPOTENCY_BUCKET_SECONDS = 60  # Coalesce events within same minute

    def __init__(self) -> None:
        self._listen_conn: psycopg.AsyncConnection | None = None
        self._listen_task: asyncio.Task | None = None
        self._agent_events: dict[str, asyncio.Event] = {}
        self._agent_wakeup_reasons: dict[str, list[str]] = {}
        self._running = False
        self._dispatching = False  # Re-entrancy guard
        self._subscriptions: dict[str, list[dict]] = {}  # agent_id -> [sub dicts]
        self._reconnect_backoff = 1.0
        self._max_reconnect_backoff = 30.0
        self._stats: dict[str, int] = {
            "notifications_received": 0,
            "wakeups_created": 0,
            "wakeups_coalesced": 0,
            "reconnects": 0,
        }

    # -- Startup / Shutdown -----------------------------------------------

    async def start(self) -> None:
        """Start the LISTEN loop on a dedicated connection.

        IMPORTANT: Uses a dedicated connection OUTSIDE the pool.
        LISTEN requires a persistent connection that stays open.
        Pool connections are returned after each query.
        """
        if _wakeup_mode() == "off":
            logger.info("WakeupQueue disabled (EVENT_WAKEUP_ENABLED=off)")
            return

        await self._load_subscriptions()
        await self._connect_listener()
        self._running = True
        self._listen_task = asyncio.create_task(
            self._listen_loop(), name="wakeup-listen"
        )
        logger.info(
            "WakeupQueue started (mode=%s, subscriptions=%d)",
            _wakeup_mode(),
            sum(len(subs) for subs in self._subscriptions.values()),
        )

    async def shutdown(self) -> None:
        """Stop the LISTEN loop and close dedicated connection."""
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._listen_conn and not self._listen_conn.closed:
            await self._listen_conn.close()
            self._listen_conn = None
        logger.info("WakeupQueue shut down. Stats: %s", self._stats)

    # -- Dedicated LISTEN connection --------------------------------------

    async def _connect_listener(self) -> None:
        """Open a dedicated async connection for LISTEN (outside pool)."""
        dsn = config.postgres.dsn
        self._listen_conn = await psycopg.AsyncConnection.connect(
            dsn, autocommit=True, row_factory=dict_row,
        )
        await self._listen_conn.execute(
            f"LISTEN {self.CHANNEL}"  # Channel name is a constant, not user input
        )
        self._reconnect_backoff = 1.0
        logger.debug("LISTEN connection established on channel %s", self.CHANNEL)

    async def _reconnect_listener(self) -> None:
        """Reconnect the LISTEN connection with exponential backoff."""
        self._stats["reconnects"] += 1
        if self._listen_conn and not self._listen_conn.closed:
            try:
                await self._listen_conn.close()
            except Exception:
                pass
            self._listen_conn = None

        wait = self._reconnect_backoff
        logger.warning(
            "LISTEN connection lost -- reconnecting in %.1fs (attempt #%d)",
            wait, self._stats["reconnects"],
        )
        await asyncio.sleep(wait)
        self._reconnect_backoff = min(
            self._reconnect_backoff * 2, self._max_reconnect_backoff
        )

        try:
            await self._connect_listener()
            logger.info("LISTEN connection re-established")
        except Exception as exc:
            logger.error("LISTEN reconnect failed: %s", exc)
            raise

    # -- LISTEN Loop ------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Main LISTEN loop -- receives NOTIFY and creates wakeup requests.

        Runs forever until shutdown. Reconnects on connection errors.
        """
        while self._running:
            try:
                # psycopg v3 async: iterate over notifications
                gen = self._listen_conn.notifies()
                async for notify in gen:
                    if not self._running:
                        break
                    await self._handle_notification(notify)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.error("LISTEN loop error: %s", exc)
                try:
                    await self._reconnect_listener()
                except Exception:
                    # Backoff will increase on next attempt
                    await asyncio.sleep(self._reconnect_backoff)

    async def _handle_notification(self, notify: Any) -> None:
        """Process a single NOTIFY payload: event_type:event_id."""
        self._stats["notifications_received"] += 1
        payload_str = notify.payload or ""
        parts = payload_str.split(":", 1)
        event_type = parts[0]
        event_id = parts[1] if len(parts) > 1 else ""

        logger.debug("NOTIFY received: event_type=%s event_id=%s", event_type, event_id)

        # Match against all subscriptions
        for agent_id, subs in self._subscriptions.items():
            for sub in subs:
                if self._matches_pattern(event_type, sub["event_pattern"]):
                    await self._create_wakeup_request(
                        agent_id=agent_id,
                        source="event",
                        event_type=event_type,
                        priority=sub["priority"],
                        context={"event_id": event_id, "event_type": event_type},
                    )

    def _matches_pattern(self, event_type: str, pattern: str) -> bool:
        """Check if event_type matches subscription pattern.

        Supports:
        - Exact match: "new_lead" matches "new_lead"
        - SQL LIKE with %: "pipeline_%" matches "pipeline_stage_complete"
        - Wildcard *: "pipeline_*" matches "pipeline_stage_complete"
        """
        if pattern == event_type:
            return True
        # Convert SQL LIKE pattern to simple check
        if "%" in pattern:
            prefix = pattern.rstrip("%")
            if event_type.startswith(prefix):
                return True
        if "*" in pattern:
            prefix = pattern.rstrip("*")
            if event_type.startswith(prefix):
                return True
        return False

    # -- Subscription Management ------------------------------------------

    async def _load_subscriptions(self) -> None:
        """Load all active subscriptions from DB into memory."""
        from shared.db import fetch_all
        rows = await fetch_all(
            """SELECT agent_id, event_pattern, priority
               FROM wakeup_subscriptions
               WHERE enabled = TRUE
               ORDER BY agent_id, priority DESC"""
        )
        self._subscriptions.clear()
        for row in rows:
            agent_id = row["agent_id"]
            if agent_id not in self._subscriptions:
                self._subscriptions[agent_id] = []
            self._subscriptions[agent_id].append({
                "event_pattern": row["event_pattern"],
                "priority": row["priority"],
            })
        logger.info(
            "Loaded %d subscriptions for %d agents",
            sum(len(s) for s in self._subscriptions.values()),
            len(self._subscriptions),
        )

    async def subscribe(self, agent_id: str, event_pattern: str, priority: int = 0) -> None:
        """Register a new subscription (persisted to DB + in-memory cache)."""
        from shared.db import execute
        await execute(
            """INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority)
               VALUES (%s, %s, %s)
               ON CONFLICT (agent_id, event_pattern) DO UPDATE
               SET priority = EXCLUDED.priority, enabled = TRUE""",
            (agent_id, event_pattern, priority),
        )
        # Update in-memory cache
        if agent_id not in self._subscriptions:
            self._subscriptions[agent_id] = []
        # Remove existing if present
        self._subscriptions[agent_id] = [
            s for s in self._subscriptions[agent_id]
            if s["event_pattern"] != event_pattern
        ]
        self._subscriptions[agent_id].append({
            "event_pattern": event_pattern,
            "priority": priority,
        })

    async def unsubscribe(self, agent_id: str, event_pattern: str) -> None:
        """Disable a subscription."""
        from shared.db import execute
        await execute(
            """UPDATE wakeup_subscriptions SET enabled = FALSE
               WHERE agent_id = %s AND event_pattern = %s""",
            (agent_id, event_pattern),
        )
        if agent_id in self._subscriptions:
            self._subscriptions[agent_id] = [
                s for s in self._subscriptions[agent_id]
                if s["event_pattern"] != event_pattern
            ]

    # -- Wakeup Request Creation ------------------------------------------

    async def _create_wakeup_request(
        self,
        agent_id: str,
        source: str,
        event_type: str | None = None,
        priority: int = 0,
        context: dict | None = None,
    ) -> None:
        """Create or coalesce a wakeup request.

        Idempotency key: agent_id:event_type:minute_bucket
        If a pending request with the same key exists, increment coalesced_count
        instead of creating a duplicate.
        """
        from psycopg.types.json import Jsonb

        from shared.db import execute, fetch_one

        minute_bucket = int(time.time()) // self.IDEMPOTENCY_BUCKET_SECONDS
        idem_key = f"{agent_id}:{event_type or source}:{minute_bucket}"

        mode = _wakeup_mode()
        status = "logged" if mode == "shadow" else "pending"

        # Try to coalesce with existing pending request
        existing = await fetch_one(
            """UPDATE wakeup_requests
               SET coalesced_count = coalesced_count + 1,
                   priority = GREATEST(priority, %s)
               WHERE idempotency_key = %s AND status IN ('pending', 'logged')
               RETURNING request_id""",
            (priority, idem_key),
        )
        if existing:
            self._stats["wakeups_coalesced"] += 1
            logger.debug(
                "Coalesced wakeup for %s (key=%s, event=%s)",
                agent_id, idem_key, event_type,
            )
        else:
            await execute(
                """INSERT INTO wakeup_requests
                       (agent_id, source, event_type, idempotency_key,
                        context, status, priority)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (idempotency_key) DO UPDATE
                   SET coalesced_count = wakeup_requests.coalesced_count + 1,
                       priority = GREATEST(wakeup_requests.priority, EXCLUDED.priority)""",
                (agent_id, source, event_type, idem_key,
                 Jsonb(context or {}), status, priority),
            )
            self._stats["wakeups_created"] += 1

        # Signal the agent's asyncio.Event so wait_for_wakeup() unblocks
        if mode == "full":
            agent_event = self._agent_events.get(agent_id)
            if agent_event:
                if agent_id not in self._agent_wakeup_reasons:
                    self._agent_wakeup_reasons[agent_id] = []
                self._agent_wakeup_reasons[agent_id].append(event_type or source)
                agent_event.set()

    async def request_wakeup(
        self,
        agent_id: str,
        source: str = "on_demand",
        event_type: str | None = None,
        priority: int = 5,
        context: dict | None = None,
    ) -> None:
        """Public API: manually request a wakeup for an agent.

        Used by other daemons to explicitly wake an agent outside of
        event-driven flow (e.g., operator command, timer-based trigger).
        """
        await self._create_wakeup_request(
            agent_id=agent_id,
            source=source,
            event_type=event_type,
            priority=priority,
            context=context,
        )

    # -- Wait for Wakeup -------------------------------------------------

    async def wait_for_wakeup(
        self,
        agent_id: str,
        timeout: float = 60.0,
    ) -> dict:
        """Block until a wakeup event fires for this agent, or timeout.

        Returns:
            {"woken_by": "event", "reasons": ["new_lead", "pipeline_stage_complete"]}
            {"woken_by": "timeout", "reasons": []}

        The timeout ensures the fallback poll ALWAYS fires -- this is the
        safety net that guarantees the scheduler never goes silent.
        """
        if agent_id not in self._agent_events:
            self._agent_events[agent_id] = asyncio.Event()
        self._agent_wakeup_reasons[agent_id] = []

        event = self._agent_events[agent_id]
        event.clear()

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            reasons = self._agent_wakeup_reasons.pop(agent_id, [])
            return {"woken_by": "event", "reasons": reasons}
        except TimeoutError:
            return {"woken_by": "timeout", "reasons": []}

    # -- Dispatch Pending Requests ----------------------------------------

    async def dispatch_pending(self, agent_id: str) -> list[dict]:
        """Fetch and mark all pending wakeup requests for an agent as dispatched.

        Called by the scheduler after waking up to know what triggered the wakeup.
        Returns list of dispatched request summaries.

        Re-entrancy guard: if already dispatching, returns empty list.
        This prevents deadlock when a dispatch handler emits events.
        """
        if self._dispatching:
            logger.debug("dispatch_pending: re-entrancy blocked for %s", agent_id)
            return []

        self._dispatching = True
        try:
            from shared.db import fetch_all

            rows = await fetch_all(
                """UPDATE wakeup_requests
                   SET status = 'dispatched', dispatched_at = NOW()
                   WHERE agent_id = %s AND status = 'pending'
                   RETURNING request_id, source, event_type,
                             coalesced_count, priority, context""",
                (agent_id,),
            )
            return [dict(r) for r in rows]
        finally:
            self._dispatching = False

    # -- Maintenance ------------------------------------------------------

    async def expire_stale_requests(self) -> int:
        """Mark expired pending requests. Returns count expired."""
        from shared.db import fetch_val
        count = await fetch_val(
            """WITH expired AS (
                   UPDATE wakeup_requests
                   SET status = 'expired'
                   WHERE status IN ('pending', 'logged')
                   AND expires_at < NOW()
                   RETURNING 1
               )
               SELECT COUNT(*) FROM expired"""
        )
        return int(count) if count else 0

    async def cleanup_old_requests(self, older_than_hours: int = 24) -> int:
        """Delete dispatched/expired requests older than threshold."""
        from shared.db import fetch_val
        count = await fetch_val(
            """WITH deleted AS (
                   DELETE FROM wakeup_requests
                   WHERE status IN ('dispatched', 'expired')
                   AND created_at < NOW() - make_interval(hours => %s)
                   RETURNING 1
               )
               SELECT COUNT(*) FROM deleted""",
            (older_than_hours,),
        )
        return int(count) if count else 0

    async def reload_subscriptions(self) -> None:
        """Hot-reload subscriptions from DB without restart."""
        await self._load_subscriptions()

    def stats(self) -> dict:
        """Return queue statistics."""
        return {
            **self._stats,
            "mode": _wakeup_mode(),
            "listening": self._listen_conn is not None and not self._listen_conn.closed,
            "agents_registered": list(self._agent_events.keys()),
            "subscriptions": {
                agent: len(subs) for agent, subs in self._subscriptions.items()
            },
        }
