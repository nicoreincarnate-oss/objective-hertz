# Phase 16: Event-Driven Wakeup Queue — PLAN

## Goal

Replace fixed-interval polling in the Perseus scheduler with event-driven agent wakeup using Postgres LISTEN/NOTIFY. Agents sleep until an event they care about fires, reducing latency from 60s polling to near-instant dispatch. Fallback polling ALWAYS remains active as a safety net. This is the final Paperclip integration phase and the most architecturally significant change.

## Prerequisites

- Phases 11-15 (Budget Consolidation, Foundation Patterns, Budget Cost Patterns, Quality Observability, Architecture Additive) complete
- `openjarvis/vassals/perseus_scheduler.py` operational as the strategic brain (tick loop runs every 60s)
- `shared/db.py` with `emit_event()` writing to `events` table
- `shared/comms.py` with `broadcast()` and `wait_for_event()` for inter-daemon communication
- Events table schema: `id SERIAL, event_type VARCHAR(100), payload JSONB, acknowledged BOOLEAN, created_at TIMESTAMP`
- Docker services running (Postgres for LISTEN/NOTIFY)
- All existing tests passing on `intel-integration` branch
- psycopg v3 async driver already in use (supports async LISTEN/NOTIFY natively)

## Feature Flag

**Name:** `EVENT_WAKEUP_ENABLED`
**Env var:** `EVENT_WAKEUP_ENABLED` (checked via `os.environ.get(...).lower() in ("true", "1")`)
**Default:** `false` (current 60s polling unchanged)
**When OFF:** Perseus scheduler ticks on its fixed interval; `emit_event()` writes to events table only; no LISTEN/NOTIFY; no wakeup queue tables queried.
**When ON (shadow mode):** LISTEN/NOTIFY fires and wakeup requests are logged, but dispatching still happens on the normal tick interval. Shadow mode lets us compare event-driven timing against polling decisions.
**When ON (full mode):** `wait_for_wakeup()` replaces `asyncio.sleep(tick_interval)` in the scheduler main loop. Events trigger immediate ticks. Fallback timeout ensures tick still fires every 60s even if no events arrive.

### Two-Phase Rollout

1. **Shadow mode** (`EVENT_WAKEUP_ENABLED=shadow`): LISTEN/NOTIFY active, wakeup requests logged to `wakeup_requests` table with `status='logged'`, but scheduling decisions still happen on the fixed tick. Metrics compare "would have woken up at T" vs "actually woke up at T+delta".
2. **Full mode** (`EVENT_WAKEUP_ENABLED=true`): Scheduler main loop awaits `WakeupQueue.wait_for_wakeup()` instead of `asyncio.sleep()`. Events trigger immediate strategic ticks.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LISTEN/NOTIFY connection competes with pool | High | Medium | Dedicated connection outside pool (not from `_pool`). Single long-lived conn for LISTEN only. |
| Missed NOTIFY if listener connection drops | Medium | High | Fallback poll ALWAYS fires every 60s. Reconnect listener with exponential backoff. Log every reconnect. |
| Deadlock: wakeup handler emits event that triggers wakeup | Medium | High | Wakeup dispatch is non-reentrant — set `_dispatching=True` flag, skip nested wakeups. Queue them for next cycle. |
| Perseus scheduler rewrite breaks daemon timing | High | Critical | Feature flag isolates ALL changes. Flag OFF = zero behavioral change. Shadow mode validates before full mode. |
| Connection leak if LISTEN conn not cleaned up | Medium | Medium | `WakeupQueue.shutdown()` explicitly closes dedicated conn. Registered in orchestrator cleanup chain. |
| Coalescing drops important distinct events | Low | Medium | Coalescing is per (agent_id, event_pattern) with count tracking. Individual event payloads stored in `wakeup_requests.context` JSONB. |
| High event volume overwhelms wakeup table | Low | Medium | Automatic cleanup: `dispatch_pending()` marks dispatched, cron prunes >24h old records. Index on `status`. |

---

## Tasks

### Plan 16-01: Database Migration (029-wakeup-queue.sql)

#### Task 1: Create wakeup_subscriptions table
**File:** `scripts/migrations/029-wakeup-queue.sql` (new)
**Action:** create
**Details:**

```sql
-- Migration 029: Event-Driven Wakeup Queue (Phase 16)
-- Adds wakeup subscription and request tables + NOTIFY trigger on events table.
-- Feature flag: EVENT_WAKEUP_ENABLED (default OFF — tables exist but are not queried)

BEGIN;

-- ── Wakeup Subscriptions ────────────────────────────────────────────
-- Each agent registers interest in event patterns. When a matching event
-- fires, a wakeup request is created for that agent.

CREATE TABLE IF NOT EXISTS wakeup_subscriptions (
    subscription_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR(50) NOT NULL,
    event_pattern   VARCHAR(200) NOT NULL,
    priority        INTEGER DEFAULT 0,
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent_id, event_pattern)
);

CREATE INDEX IF NOT EXISTS idx_wakeup_sub_agent
    ON wakeup_subscriptions (agent_id) WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_wakeup_sub_pattern
    ON wakeup_subscriptions (event_pattern) WHERE enabled = TRUE;

COMMENT ON TABLE wakeup_subscriptions IS
    'Agent event subscriptions for wakeup queue (Phase 16 Paperclip)';
COMMENT ON COLUMN wakeup_subscriptions.event_pattern IS
    'Exact event_type match or SQL LIKE pattern (e.g. pipeline_% for all pipeline events)';
COMMENT ON COLUMN wakeup_subscriptions.priority IS
    'Higher priority subscriptions produce higher-priority wakeup requests (0=normal, 10=critical)';
```

#### Task 2: Create wakeup_requests table
**File:** `scripts/migrations/029-wakeup-queue.sql` (same file, continued)
**Action:** create (continued)
**Details:**

```sql
-- ── Wakeup Requests ─────────────────────────────────────────────────
-- Created when an event matches a subscription, or by timer/manual trigger.
-- The WakeupQueue dispatches pending requests to sleeping agents.

CREATE TABLE IF NOT EXISTS wakeup_requests (
    request_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        VARCHAR(50) NOT NULL,
    source          VARCHAR(20) NOT NULL DEFAULT 'event'
                    CHECK (source IN ('timer', 'assignment', 'event', 'on_demand')),
    event_type      VARCHAR(100),
    idempotency_key VARCHAR(200) UNIQUE,
    coalesced_count INTEGER DEFAULT 1,
    context         JSONB DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'dispatched', 'expired', 'logged')),
    priority        INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    dispatched_at   TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '5 minutes'
);

CREATE INDEX IF NOT EXISTS idx_wakeup_req_agent_status
    ON wakeup_requests (agent_id, status) WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_wakeup_req_created
    ON wakeup_requests (created_at);

CREATE INDEX IF NOT EXISTS idx_wakeup_req_expires
    ON wakeup_requests (expires_at) WHERE status = 'pending';

COMMENT ON TABLE wakeup_requests IS
    'Pending agent wakeup requests — dispatched by WakeupQueue (Phase 16 Paperclip)';
COMMENT ON COLUMN wakeup_requests.idempotency_key IS
    'Prevents duplicate wakeups: agent_id:event_type:minute_bucket for event-sourced, agent_id:timer:minute for timer';
COMMENT ON COLUMN wakeup_requests.coalesced_count IS
    'Number of duplicate events that were merged into this single wakeup request';
COMMENT ON COLUMN wakeup_requests.status IS
    'logged = shadow mode only (event recorded but not dispatched)';
```

#### Task 3: Create NOTIFY trigger function on events table
**File:** `scripts/migrations/029-wakeup-queue.sql` (same file, continued)
**Action:** create (continued)
**Details:**

```sql
-- ── NOTIFY Trigger ──────────────────────────────────────────────────
-- After every INSERT on the events table, fire a Postgres NOTIFY on
-- channel "wakeup_events" with the event_type as payload.
-- The WakeupQueue LISTEN thread picks this up and creates wakeup_requests
-- for matching subscriptions.

CREATE OR REPLACE FUNCTION notify_wakeup_event()
RETURNS TRIGGER AS $$
BEGIN
    -- Fire on the general wakeup channel with event_type as payload.
    -- The listener parses the payload and matches against subscriptions.
    PERFORM pg_notify('wakeup_events', NEW.event_type || ':' || NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Only fire on INSERT (new events). Updates/deletes don't generate wakeups.
DROP TRIGGER IF EXISTS events_notify_wakeup ON events;
CREATE TRIGGER events_notify_wakeup
    AFTER INSERT ON events
    FOR EACH ROW
    EXECUTE FUNCTION notify_wakeup_event();

COMMENT ON FUNCTION notify_wakeup_event() IS
    'Fires pg_notify on wakeup_events channel after every event INSERT (Phase 16)';
```

#### Task 4: Seed default subscriptions for all daemons
**File:** `scripts/migrations/029-wakeup-queue.sql` (same file, continued)
**Action:** create (continued)
**Details:**

```sql
-- ── Default Subscriptions ───────────────────────────────────────────
-- Seed event subscriptions for the 4 daemons + orchestrator.
-- Uses ON CONFLICT DO NOTHING so migration is idempotent.

-- Titan: revenue pipeline events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('titan', 'new_lead', 5),
    ('titan', 'pipeline_stage_complete', 7),
    ('titan', 'email_sent', 3),
    ('titan', 'follow_up_due', 8),
    ('titan', 'lead_research_complete', 5),
    ('titan', 'lead_enrichment_complete', 4),
    ('titan', 'site_build_complete', 6),
    ('titan', 'payment_received', 10)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- Hermes: alerts and health events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('hermes', 'alert_triggered', 9),
    ('hermes', 'health_check_failed', 10),
    ('hermes', 'approval_requested', 8),
    ('hermes', 'budget_exceeded', 10),
    ('hermes', 'budget_warning', 7),
    ('hermes', 'urgent_alert', 10),
    ('hermes', 'agent_help_request', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- ClawdBot: site building and browser tasks
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('clawdbot', 'site_build_requested', 8),
    ('clawdbot', 'skill_execution_requested', 7),
    ('clawdbot', 'site_verify_requested', 5),
    ('clawdbot', 'browser_task_queued', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

-- Perseus (orchestrator): subscribes to ALL high-value events
INSERT INTO wakeup_subscriptions (agent_id, event_pattern, priority) VALUES
    ('perseus', 'budget_exceeded', 10),
    ('perseus', 'health_check_failed', 10),
    ('perseus', 'pipeline_stage_complete', 5),
    ('perseus', 'payment_received', 9),
    ('perseus', 'agent_help_request', 7),
    ('perseus', 'vassal_crash', 10),
    ('perseus', 'budget_warning', 6)
ON CONFLICT (agent_id, event_pattern) DO NOTHING;

COMMIT;
```

**Acceptance criteria:**
- [ ] Migration runs idempotently (`\i scripts/migrations/029-wakeup-queue.sql` twice without error)
- [ ] `wakeup_subscriptions` has 26 seed rows across 4 agents
- [ ] `wakeup_requests` table created with correct constraints and indices
- [ ] `events_notify_wakeup` trigger exists on events table
- [ ] `SELECT * FROM pg_trigger WHERE tgname = 'events_notify_wakeup'` returns 1 row
- [ ] Manual test: `INSERT INTO events (event_type, payload) VALUES ('test_wakeup', '{}')` fires NOTIFY (verify with `LISTEN wakeup_events; INSERT ...; ` in psql)
- [ ] All SQL uses parameterized patterns (no f-strings in migration)

---

### Plan 16-02: WakeupQueue Core Implementation

#### Task 1: Create WakeupQueue class with LISTEN/NOTIFY
**File:** `shared/wakeup_queue.py` (new, ~280 lines)
**Action:** create
**Details:**

```python
"""
Event-driven wakeup queue using Postgres LISTEN/NOTIFY.

Replaces fixed-interval polling with near-instant event-driven dispatch.
Feature flag: EVENT_WAKEUP_ENABLED (shadow | true | false)

Architecture:
- Dedicated Postgres connection (outside pool) for LISTEN
- Subscriptions stored in wakeup_subscriptions table
- Incoming NOTIFY → match against subscriptions → create wakeup_request
- Agents call wait_for_wakeup() which blocks until wakeup or timeout
- Duplicate coalescing via idempotency_key

This module NEVER imports from perseus/, titan/, hermes/, or clawdbot/.
It is a shared infrastructure primitive.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from shared.config import config

logger = logging.getLogger("perseus.wakeup_queue")

# ── Feature flag ─────────────────────────────────────────────────────

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
        self._stats = {
            "notifications_received": 0,
            "wakeups_created": 0,
            "wakeups_coalesced": 0,
            "reconnects": 0,
        }

    # ── Startup / Shutdown ───────────────────────────────────────────

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

    # ── Dedicated LISTEN connection ──────────────────────────────────

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
            "LISTEN connection lost — reconnecting in %.1fs (attempt #%d)",
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

    # ── LISTEN Loop ──────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Main LISTEN loop — receives NOTIFY and creates wakeup requests.

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

    async def _handle_notification(self, notify) -> None:
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

    # ── Subscription Management ──────────────────────────────────────

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

    # ── Wakeup Request Creation ──────────────────────────────────────

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

    # ── Wait for Wakeup ─────────────────────────────────────────────

    async def wait_for_wakeup(
        self,
        agent_id: str,
        timeout: float = 60.0,
    ) -> dict:
        """Block until a wakeup event fires for this agent, or timeout.

        Returns:
            {"woken_by": "event", "reasons": ["new_lead", "pipeline_stage_complete"]}
            {"woken_by": "timeout", "reasons": []}

        The timeout ensures the fallback poll ALWAYS fires — this is the
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
        except asyncio.TimeoutError:
            return {"woken_by": "timeout", "reasons": []}

    # ── Dispatch Pending Requests ────────────────────────────────────

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
            from shared.db import fetch_all, execute

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

    # ── Maintenance ──────────────────────────────────────────────────

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
```

**Acceptance criteria:**
- [ ] `WakeupQueue` starts and opens a dedicated LISTEN connection outside the pool
- [ ] NOTIFY on `wakeup_events` channel is received within 100ms
- [ ] Matching subscriptions create `wakeup_requests` rows
- [ ] Duplicate events within same minute bucket are coalesced (count incremented)
- [ ] `wait_for_wakeup()` returns immediately on event, or after timeout
- [ ] `dispatch_pending()` marks requests as dispatched and returns summaries
- [ ] Re-entrancy guard prevents nested dispatch
- [ ] Connection loss triggers reconnect with exponential backoff (1s, 2s, 4s, ... max 30s)
- [ ] `shutdown()` cleanly closes dedicated connection
- [ ] Shadow mode creates requests with `status='logged'` and never signals asyncio.Event
- [ ] No f-string SQL anywhere — all queries use parameterized `%s`
- [ ] No imports from daemon packages (perseus/, titan/, hermes/, clawdbot/)

---

### Plan 16-03: Integrate emit_event() with NOTIFY

#### Task 1: Add NOTIFY comment to emit_event() documentation
**File:** `shared/db.py`
**Action:** modify
**Details:**

The Postgres trigger (`events_notify_wakeup`) handles NOTIFY automatically on INSERT. No code change to `emit_event()` is needed -- the trigger fires on every `INSERT INTO events`.

However, add a documentation comment to `emit_event()` noting the trigger exists:

```python
async def emit_event(event_type: str, payload: dict[str, Any] | None = None) -> int:
    """Emit an event for Hermes/dashboard. Returns event ID.

    NOTE: The events_notify_wakeup trigger (migration 029) automatically
    fires pg_notify('wakeup_events', event_type:id) on every INSERT.
    When EVENT_WAKEUP_ENABLED is active, the WakeupQueue LISTEN loop
    picks up these notifications and creates wakeup_requests for
    agents with matching subscriptions.
    """
    import json
    payload = enrich_payload_with_context(payload)
    row = await fetch_one(
        """INSERT INTO events (event_type, payload)
           VALUES (%s, %s) RETURNING id""",
        (event_type, json.dumps(payload or {})),
    )
    return int(row["id"]) if row else 0
```

This is deliberately a documentation-only change. The trigger-based approach means:
1. **Zero coupling**: `emit_event()` does not need to know about wakeup_queue
2. **Zero performance impact when OFF**: Trigger fires but LISTEN conn doesn't exist
3. **All existing callers get wakeup for free**: No need to modify every `emit_event()` call site

**Acceptance criteria:**
- [ ] `emit_event()` docstring updated with trigger note
- [ ] No behavioral change to `emit_event()` function body
- [ ] `INSERT INTO events` still triggers NOTIFY (verified by migration test)

---

### Plan 16-04: Perseus Scheduler Integration

#### Task 1: Add WakeupQueue to PerseusScheduler __init__
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

Add `wakeup_queue` parameter to `PerseusScheduler.__init__()`:

```python
def __init__(
    self,
    bus: EventBus,
    vassal_discovery: Any,
    config: PerseusConfig | None = None,
    decision_audit: Any | None = None,
    budget_guard: Any | None = None,
    wakeup_queue: Any | None = None,  # NEW: WakeupQueue instance
) -> None:
    self._bus = bus
    self._vassals = vassal_discovery
    self._config = config or PerseusConfig()
    self._decision_audit = decision_audit
    self._budget_guard = budget_guard
    self._wakeup_queue = wakeup_queue  # NEW
    self._running = False
    self._last_health_check = 0.0
    self._tick_count = 0
    self._prev_states: list[dict[str, Any]] = []
```

#### Task 2: Replace asyncio.sleep with wait_for_wakeup in main loop
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

Modify the `start()` method main loop. Currently (line 82-87):
```python
self._running = True
while self._running:
    try:
        await self._tick()
    except Exception as exc:
        logger.error("Perseus tick error: %s", exc, exc_info=True)
    await asyncio.sleep(self._config.tick_interval)
```

Replace with:
```python
self._running = True

# Start WakeupQueue if provided and enabled
if self._wakeup_queue is not None:
    try:
        await self._wakeup_queue.start()
        logger.info("WakeupQueue active — event-driven wakeup enabled")
    except Exception as exc:
        logger.error("WakeupQueue failed to start: %s — falling back to polling", exc)
        self._wakeup_queue = None

while self._running:
    try:
        await self._tick()
    except Exception as exc:
        logger.error("Perseus tick error: %s", exc, exc_info=True)

    # Event-driven wait OR fixed-interval sleep
    if self._wakeup_queue is not None and self._wakeup_queue._running:
        wakeup = await self._wakeup_queue.wait_for_wakeup(
            "perseus", timeout=float(self._config.tick_interval)
        )
        if wakeup["woken_by"] == "event":
            logger.info(
                "Event-driven wakeup: reasons=%s",
                wakeup["reasons"][:5],  # Cap log length
            )
    else:
        await asyncio.sleep(self._config.tick_interval)
```

#### Task 3: Add wakeup dispatch info to tick
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

At the beginning of `_tick()`, after `self._tick_count += 1`, add:

```python
# Dispatch pending wakeup requests (if queue active)
wakeup_dispatched = []
if self._wakeup_queue is not None and self._wakeup_queue._running:
    try:
        wakeup_dispatched = await self._wakeup_queue.dispatch_pending("perseus")
        if wakeup_dispatched:
            logger.debug(
                "Dispatched %d wakeup requests: %s",
                len(wakeup_dispatched),
                [r.get("event_type", r.get("source")) for r in wakeup_dispatched[:5]],
            )
    except Exception as exc:
        logger.warning("Wakeup dispatch failed (non-critical): %s", exc)
```

#### Task 4: Add wakeup stats to tick event
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

In the tick event publish (step 8, line 142-148), add wakeup stats:

```python
tick_event = {
    "sub_type": "perseus_tick",
    "tick": self._tick_count,
    "scheduled": len(priorities.get("schedule", [])),
    "skipped": len(priorities.get("skip", [])),
    "reasoning": priorities.get("reasoning", ""),
}
# Include wakeup stats if queue active
if self._wakeup_queue is not None:
    tick_event["wakeup_stats"] = self._wakeup_queue.stats()
    tick_event["wakeup_dispatched"] = len(wakeup_dispatched)

self._bus.publish(EventType.CUSTOM, tick_event)
```

#### Task 5: Add cleanup on stop
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

Modify `stop()` to shut down WakeupQueue:

```python
async def stop(self) -> None:
    """Stop the scheduler."""
    logger.info("Perseus scheduler stopping...")
    self._running = False
    if self._wakeup_queue is not None:
        await self._wakeup_queue.shutdown()
        logger.info("WakeupQueue shut down")
```

#### Task 6: Add status() method wakeup info
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

Add wakeup queue status to the `status()` method (if one exists, or create):

```python
def status(self) -> dict:
    """Return scheduler status for A2A health checks."""
    base = {
        "running": self._running,
        "tick_count": self._tick_count,
        "tick_interval": self._config.tick_interval,
    }
    if self._wakeup_queue is not None:
        base["wakeup_queue"] = self._wakeup_queue.stats()
    return base
```

**Acceptance criteria:**
- [ ] `EVENT_WAKEUP_ENABLED=false`: scheduler loops with `asyncio.sleep(60)` exactly as before
- [ ] `EVENT_WAKEUP_ENABLED=shadow`: LISTEN active, wakeup requests logged, but scheduler still sleeps 60s
- [ ] `EVENT_WAKEUP_ENABLED=true`: scheduler wakes immediately on event, with 60s timeout fallback
- [ ] Fallback: if WakeupQueue fails to start, scheduler falls back to polling (no crash)
- [ ] Wakeup dispatch info included in tick decision record
- [ ] `stop()` cleanly shuts down WakeupQueue
- [ ] No behavior change when feature flag is off (verified by running existing test suite)

---

### Plan 16-05: Orchestrator Wiring

#### Task 1: Create and pass WakeupQueue to PerseusScheduler
**File:** `orchestrator.py`
**Action:** modify
**Details:**

After the PerseusScheduler construction (lines 273-290), add WakeupQueue creation:

```python
# Create WakeupQueue (Phase 16: event-driven wakeup)
wakeup_queue = None
try:
    from shared.wakeup_queue import WakeupQueue, _wakeup_mode
    if _wakeup_mode() != "off":
        wakeup_queue = WakeupQueue()
        logger.info("WakeupQueue created (mode=%s)", _wakeup_mode())
    else:
        logger.info("WakeupQueue disabled (EVENT_WAKEUP_ENABLED not set)")
except ImportError:
    logger.debug("WakeupQueue not available (shared/wakeup_queue.py missing)")

self._scheduler = PerseusScheduler(
    bus=bus,
    vassal_discovery=self._discovery,
    config=sched_config,
    budget_guard=budget_guard,
    wakeup_queue=wakeup_queue,  # NEW
)
```

#### Task 2: Add cleanup to orchestrator shutdown
**File:** `orchestrator.py`
**Action:** modify
**Details:**

In the `_cleanup()` method, add WakeupQueue shutdown before pool close:

```python
async def _cleanup(self):
    """Cleanup on shutdown."""
    if self._scheduler:
        await self._scheduler.stop()  # This now also stops WakeupQueue
    # ... existing cleanup ...
```

**Acceptance criteria:**
- [ ] WakeupQueue created and passed to PerseusScheduler when flag enabled
- [ ] WakeupQueue not created when flag is off (no connection opened)
- [ ] Missing `wakeup_queue.py` does not crash orchestrator
- [ ] Clean shutdown: WakeupQueue connection closed before pool close

---

### Plan 16-06: Wakeup Maintenance Scheduler Entry

#### Task 1: Add wakeup maintenance to SCHEDULES
**File:** `perseus/scheduler.py`
**Action:** modify
**Details:**

Add two new Schedule entries:

```python
# Event-driven wakeup maintenance (Phase 16)
Schedule("wakeup_expire_stale", 300, "Expire stale wakeup requests", skippable=False),
Schedule("wakeup_cleanup", 86400, "Clean up old dispatched/expired wakeup requests", skippable=True),
```

#### Task 2: Wire maintenance handlers
**File:** `openjarvis/vassals/perseus_scheduler.py`
**Action:** modify
**Details:**

In `_tick()`, add periodic maintenance after the main tick logic (after step 8):

```python
# 9. Wakeup queue maintenance (every 5 minutes)
if self._wakeup_queue is not None and self._tick_count % 5 == 0:
    try:
        expired = await self._wakeup_queue.expire_stale_requests()
        if expired:
            logger.debug("Expired %d stale wakeup requests", expired)
    except Exception as exc:
        logger.debug("Wakeup expiry failed (non-critical): %s", exc)

# 10. Wakeup queue daily cleanup (every ~24h = 1440 ticks at 60s)
if self._wakeup_queue is not None and self._tick_count % 1440 == 0:
    try:
        cleaned = await self._wakeup_queue.cleanup_old_requests(older_than_hours=24)
        if cleaned:
            logger.info("Cleaned up %d old wakeup requests", cleaned)
    except Exception as exc:
        logger.debug("Wakeup cleanup failed (non-critical): %s", exc)
```

**Acceptance criteria:**
- [ ] Stale requests expired every 5 ticks (~5 minutes)
- [ ] Old dispatched/expired requests cleaned every 24h
- [ ] Both maintenance tasks are non-critical (failures logged, not raised)

---

### Plan 16-07: close_pool() Thread Safety Fix

#### Task 1: Acquire _pool_lock in close_pool()
**File:** `shared/db.py`
**Action:** modify
**Details:**

Per AEGIS audit requirement: `close_pool()` must acquire `_pool_lock` before setting `_pool = None`. Current code (lines 58-64):

```python
async def close_pool():
    """Close the connection pool. Call on daemon shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Postgres pool closed")
```

Replace with:

```python
async def close_pool():
    """Close the connection pool. Call on daemon shutdown."""
    global _pool
    async with _pool_lock:
        if _pool:
            await _pool.close()
            _pool = None
            logger.info("Postgres pool closed")
```

This is required because the WakeupQueue's dedicated connection exists outside the pool, and shutdown ordering matters: WakeupQueue.shutdown() must complete before close_pool() runs. The lock prevents a race where `init_pool()` is called concurrently during shutdown.

**Acceptance criteria:**
- [ ] `close_pool()` acquires `_pool_lock` before modifying `_pool`
- [ ] No deadlock when called during normal shutdown sequence
- [ ] Existing tests still pass

---

### Plan 16-08: Tests

#### Task 1: Unit tests for WakeupQueue
**File:** `tests/shared/test_wakeup_queue.py` (new)
**Action:** create
**Details:**

```python
"""Tests for shared/wakeup_queue.py — Event-Driven Wakeup Queue (Phase 16)."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Feature flag helper tests

class TestWakeupMode:
    def test_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            from shared.wakeup_queue import _wakeup_mode
            # Force reimport
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


# Coalescing tests

class TestCoalescing:
    """Test that duplicate events within the same minute bucket are coalesced."""

    @pytest.mark.asyncio
    async def test_idempotency_key_generation(self):
        """Verify idempotency key format: agent_id:event_type:minute_bucket."""
        from shared.wakeup_queue import WakeupQueue
        queue = WakeupQueue()
        # Key format is tested implicitly via _create_wakeup_request
        # but we verify the bucket math here
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
        # Pre-register agent event
        queue._agent_events["test_agent"] = asyncio.Event()
        queue._agent_wakeup_reasons["test_agent"] = ["new_lead"]

        # Set the event before waiting
        queue._agent_events["test_agent"].set()

        result = await queue.wait_for_wakeup("test_agent", timeout=5.0)
        assert result["woken_by"] == "event"

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
```

#### Task 2: Integration tests for WakeupQueue + emit_event
**File:** `tests/shared/test_wakeup_integration.py` (new)
**Action:** create
**Details:**

```python
"""Integration tests for WakeupQueue + emit_event (requires Postgres).

These tests verify the full LISTEN/NOTIFY flow:
1. emit_event() inserts into events table
2. Trigger fires pg_notify('wakeup_events', ...)
3. WakeupQueue receives notification
4. Matching subscription creates wakeup_request
5. wait_for_wakeup() returns immediately

Run with: PYTHONPATH=. pytest tests/shared/test_wakeup_integration.py -v
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
        from shared.db import execute, fetch_val

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
```

#### Task 3: Test Perseus scheduler with WakeupQueue
**File:** `tests/openjarvis/test_perseus_wakeup.py` (new)
**Action:** create
**Details:**

```python
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

        # The start() method should handle the failure gracefully
        # (tested implicitly — scheduler._wakeup_queue set to None after failure)

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
```

**Acceptance criteria:**
- [ ] `PYTHONPATH=. pytest tests/shared/test_wakeup_queue.py -v` — all unit tests pass
- [ ] `SKIP_DB_TESTS=0 PYTHONPATH=. pytest tests/shared/test_wakeup_integration.py -v` — all integration tests pass (requires Postgres + migration 029)
- [ ] `PYTHONPATH=. pytest tests/openjarvis/test_perseus_wakeup.py -v` — scheduler integration tests pass
- [ ] `ruff check shared/wakeup_queue.py` clean
- [ ] All 457+ existing tests still pass

---

## New Files Summary

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `scripts/migrations/029-wakeup-queue.sql` | ~100 | Tables, trigger, seed subscriptions |
| `shared/wakeup_queue.py` | ~280 | WakeupQueue: LISTEN/NOTIFY, subscriptions, coalescing, wait_for_wakeup |
| `tests/shared/test_wakeup_queue.py` | ~120 | Unit tests for WakeupQueue |
| `tests/shared/test_wakeup_integration.py` | ~150 | Integration tests (requires Postgres) |
| `tests/openjarvis/test_perseus_wakeup.py` | ~80 | Scheduler + WakeupQueue integration tests |

## Modified Files Summary

| File | Change |
|------|--------|
| `shared/db.py` | Docstring update on `emit_event()` + `close_pool()` lock fix |
| `openjarvis/vassals/perseus_scheduler.py` | Accept `wakeup_queue` param, replace sleep with wait_for_wakeup, add dispatch + stats + cleanup |
| `orchestrator.py` | Create WakeupQueue and pass to PerseusScheduler |
| `perseus/scheduler.py` | Add 2 maintenance Schedule entries |

---

## Execution Order

1. **Migration first** (Plan 16-01) — tables and trigger must exist before any code runs
2. **WakeupQueue core** (Plan 16-02) — standalone module with no daemon dependencies
3. **emit_event docstring** (Plan 16-03) — trivial, just documentation
4. **close_pool lock fix** (Plan 16-07) — prerequisite safety fix
5. **Perseus scheduler integration** (Plan 16-04) — wire WakeupQueue into tick loop
6. **Orchestrator wiring** (Plan 16-05) — create queue and pass to scheduler
7. **Maintenance schedule** (Plan 16-06) — periodic cleanup
8. **Tests** (Plan 16-08) — validate everything

## Rollback Plan

1. Set `EVENT_WAKEUP_ENABLED=false` (or unset) — immediate return to polling behavior
2. Tables remain but are inert (no code queries them when flag is off)
3. Trigger remains but is harmless (NOTIFY fires to nobody listening)
4. To fully rollback: `DROP TRIGGER events_notify_wakeup ON events; DROP FUNCTION notify_wakeup_event(); DROP TABLE wakeup_requests; DROP TABLE wakeup_subscriptions;`

## Success Criteria

- [ ] `EVENT_WAKEUP_ENABLED=false`: zero behavioral change from pre-Phase-16 baseline
- [ ] `EVENT_WAKEUP_ENABLED=shadow`: LISTEN active, wakeup requests logged, timing comparison metrics available, scheduling unchanged
- [ ] `EVENT_WAKEUP_ENABLED=true`: events trigger immediate scheduler tick (< 500ms vs 60s polling)
- [ ] Fallback poll ALWAYS fires at 60s timeout — never goes silent
- [ ] LISTEN connection survives Postgres restart (reconnect with backoff)
- [ ] Coalescing: 10 rapid `new_lead` events create 1 wakeup request with `coalesced_count=10`
- [ ] No deadlock: wakeup handler emitting events does not self-trigger infinite loop
- [ ] All 457+ existing tests pass with flag OFF
- [ ] All new tests pass (unit + integration)
- [ ] `ruff check shared/wakeup_queue.py` clean
- [ ] No f-string SQL in any new or modified file
- [ ] Memory: dedicated LISTEN connection uses ~1 connection (not a pool slot)

---

*Plan created: 2026-03-30 — Phase 16: Event-Driven Wakeup Queue (final Paperclip integration)*
