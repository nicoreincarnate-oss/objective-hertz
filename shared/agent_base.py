"""
Base class for all Perseus agents.
Every agent (Titan, Hermes, OpenClaw, future agents) implements this interface
and registers with Perseus.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from shared import db
from shared.agent_state import AgentState, PauseReason, validate_transition
from shared.observability import (
    bind_context_from_payload,
    capture_exception,
    configure_service_observability,
    observe_work_duration,
    record_agent_shutdown,
    record_agent_started,
    record_event_emitted,
    record_task_claimed,
    record_task_completed,
    record_task_failed,
    set_active_work,
)

if TYPE_CHECKING:
    from shared.daemon_memory import DaemonMemoryStore, MemoryCache, WorkingMemory


def _deerflow_memory_enabled() -> bool:
    """Check ENABLE_DEERFLOW_MEMORY feature flag."""
    return os.environ.get("ENABLE_DEERFLOW_MEMORY", "").lower() in ("true", "1")


def _agent_state_enabled() -> bool:
    """Check AGENT_STATE_MACHINE_ENABLED feature flag."""
    return os.environ.get("AGENT_STATE_MACHINE_ENABLED", "").lower() in ("true", "1")


def _atomic_checkout_enabled() -> bool:
    """Check ATOMIC_CHECKOUT_ENABLED feature flag."""
    return os.environ.get("ATOMIC_CHECKOUT_ENABLED", "").lower() in ("true", "1")


def _recursion_guard_enabled() -> bool:
    """Check RECURSION_GUARD_ENABLED feature flag."""
    return os.environ.get("RECURSION_GUARD_ENABLED", "").lower() in ("true", "1")


def _session_health_enabled() -> bool:
    """Check SESSION_HEALTH_ENABLED feature flag."""
    return os.environ.get("SESSION_HEALTH_ENABLED", "").lower() in ("true", "1")


class AgentBase(ABC):
    """Base class for Perseus agents."""

    name: str = "unnamed"
    description: str = ""

    def __init__(self):
        self.logger = logging.getLogger(f"perseus.{self.name}")
        self._running = False
        self._shutdown_requested = False
        self._active_work: set[str] = set()
        self._work_started_at: dict[str, float] = {}
        self._active_work_drained = asyncio.Event()
        self._active_work_drained.set()
        self._stopped = asyncio.Event()
        self._stopped.set()
        self._shutdown_timeout_seconds = 45
        configure_service_observability(self.name)
        # OJ EventBus integration
        try:
            from shared.oj_bridge import get_bus
            self._bus = get_bus()
        except Exception:
            self._bus = None

        # Agent State Machine (Phase 12: FP-01)
        self._state: AgentState = AgentState.IDLE
        self._pause_reason: PauseReason | None = None

        # Heartbeat lifecycle (Phase 14: QUAL-10)
        self._heartbeat = None  # HeartbeatEmitter, lazily created

        # DeerFlow persistent memory (Phase 3)
        self._memory: DaemonMemoryStore | None = None
        self._memory_cache: MemoryCache | None = None
        self._working_memory: WorkingMemory | None = None

    @abstractmethod
    async def start(self):
        """Start the agent's main loop."""
        ...

    @abstractmethod
    async def stop(self):
        """Gracefully stop the agent."""
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        """Return health status."""
        ...

    async def _load_memory(self) -> None:
        """Load persistent memory during registration (DeerFlow Phase 3).

        Reads from JSON cache first (fast startup), then reconciles with
        Postgres in the background. Gated behind ENABLE_DEERFLOW_MEMORY.
        """
        if not _deerflow_memory_enabled():
            return

        try:
            from shared.daemon_memory import DaemonMemoryStore, MemoryCache, WorkingMemory

            self._memory = DaemonMemoryStore()
            self._memory_cache = MemoryCache(self._memory)
            self._working_memory = WorkingMemory()

            # Fast path: load from JSON cache
            cached = await self._memory_cache.load_cache(self.name)
            for key, value in cached.items():
                self._working_memory.set(key, value)

            self.logger.info(
                "DeerFlow memory loaded for %s (%d cached entries)",
                self.name, len(cached),
            )

            # Background reconciliation with Postgres
            asyncio.create_task(self._reconcile_memory())
        except Exception as exc:
            self.logger.warning("DeerFlow memory load failed for %s: %s", self.name, exc)
            # Non-fatal — daemon operates without memory
            self._memory = None
            self._memory_cache = None
            self._working_memory = None

    async def _reconcile_memory(self) -> None:
        """Background task: rebuild cache from Postgres (authoritative source)."""
        if self._memory_cache is None:
            return
        try:
            fresh = await self._memory_cache.rebuild_cache(self.name)
            if self._working_memory is not None:
                for key, value in fresh.items():
                    self._working_memory.set(key, value)
        except Exception as exc:
            self.logger.debug("Memory reconciliation skipped for %s: %s", self.name, exc)

    async def _save_memory(self) -> None:
        """Persist working memory to episodic store during deregistration.

        Saves all current working memory entries as episodic memories
        so they survive daemon restarts. Gated behind ENABLE_DEERFLOW_MEMORY.
        """
        if not _deerflow_memory_enabled():
            return
        if self._working_memory is None or self._memory is None:
            return

        try:
            # Flush session health before persisting memory entries
            await self._flush_session_health()

            items = self._working_memory.items()
            for key, value in items:
                content = value if isinstance(value, dict) else {"value": value}
                await self._memory.save_episodic(self.name, key, content)

            # Update cache
            if self._memory_cache is not None:
                cache_data = {k: v for k, v in items}
                await self._memory_cache.save_cache(self.name, cache_data)

            self.logger.info(
                "DeerFlow memory saved for %s (%d entries persisted)",
                self.name, len(items),
            )
        except Exception as exc:
            self.logger.warning("DeerFlow memory save failed for %s: %s", self.name, exc)

    # -- Session Health (Phase 12: FP-05) -------------------------------------

    async def _flush_session_health(self) -> None:
        """Persist current session health metrics to DB."""
        if not _session_health_enabled() or self._working_memory is None:
            return
        try:
            import uuid

            from psycopg.types.json import Jsonb

            snapshot = self._working_memory.health_snapshot()
            await db.execute(
                """INSERT INTO session_health (session_id, agent_id, metrics)
                   VALUES (%s, %s, %s)""",
                (str(uuid.uuid4()), self.name, Jsonb(snapshot)),
            )
        except Exception as exc:
            self.logger.debug("Session health flush failed: %s", exc)

    # -- Heartbeat Lifecycle (Phase 14: QUAL-10) --------------------------------

    async def _start_heartbeat(self) -> None:
        """Start heartbeat emission. Called when agent begins active work."""
        if self._heartbeat is None:
            from shared.heartbeat import HeartbeatEmitter, alert_stale_daemon

            self._heartbeat = HeartbeatEmitter(
                daemon_name=self.name,
                on_stale=alert_stale_daemon,
            )
        await self._heartbeat.start()

    async def _stop_heartbeat(self) -> None:
        """Stop heartbeat emission. Called when agent drains active work."""
        if self._heartbeat is not None:
            await self._heartbeat.stop()

    # -- Agent State Machine (Phase 12: FP-01) --------------------------------

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def pause_reason(self) -> PauseReason | None:
        return self._pause_reason

    async def _transition(
        self,
        new_state: AgentState,
        pause_reason: PauseReason | None = None,
    ) -> bool:
        """Attempt a state transition. Returns True if successful.

        When AGENT_STATE_MACHINE_ENABLED is off, always returns True (no-op).
        """
        if not _agent_state_enabled():
            return True

        old_state = self._state
        if not validate_transition(old_state, new_state):
            self.logger.warning(
                "Invalid state transition %s -> %s for %s",
                old_state.value, new_state.value, self.name,
            )
            return False

        self._state = new_state
        self._pause_reason = pause_reason if new_state == AgentState.PAUSED else None

        # Persist to DB
        try:
            await db.execute(
                "UPDATE agent_registry SET state = %s, pause_reason = %s WHERE name = %s",
                (new_state.value, pause_reason.value if pause_reason else None, self.name),
            )
        except Exception as exc:
            self.logger.debug("State persist failed (non-fatal): %s", exc)

        # Emit event
        try:
            await self.emit_event("agent_state_change", {
                "from": old_state.value,
                "to": new_state.value,
                "pause_reason": pause_reason.value if pause_reason else None,
            })
        except Exception:
            pass  # event emission is best-effort

        return True

    async def register(self):
        """Register this agent with Perseus via DB, and optionally create a Conway wallet."""
        record_agent_started(self.name)
        await db.execute(
            """INSERT INTO agent_registry (name, description, status)
               VALUES (%s, %s, 'active')
               ON CONFLICT (name) DO UPDATE SET status = 'active', updated_at = NOW()""",
            (self.name, self.description),
        )
        self.logger.info(f"Agent '{self.name}' registered")
        await self._transition(AgentState.IDLE)

        # DeerFlow: load persistent memory
        await self._load_memory()

        # Conway wallet provisioning
        try:
            from shared.config import config
            if config.conway.enabled:
                from conway.runtime import ensure_agent_runtime

                conway_state = await ensure_agent_runtime(
                    self.name,
                    agent_card={
                        "name": self.name,
                        "description": self.description,
                    },
                )
                wallet_address = conway_state.get("wallet_address", "")
                if wallet_address:
                    self.logger.info("Conway wallet: %s", wallet_address)
                if conway_state.get("api_key_provisioned"):
                    self.logger.info("Conway Cloud identity provisioned for %s", self.name)
                if conway_state.get("erc8004_registered"):
                    self.logger.info("ERC-8004 identity active for %s", self.name)
        except Exception as e:
            self.logger.debug(f"Conway wallet setup skipped: {e}")
            capture_exception(e, service_name=self.name, category="wallet_setup")

    async def deregister(self):
        """Mark agent as inactive."""
        # DeerFlow: persist working memory before going inactive
        await self._save_memory()

        await db.execute(
            "UPDATE agent_registry SET status = 'inactive', updated_at = NOW() WHERE name = %s",
            (self.name,),
        )

    async def emit_event(self, event_type: str, payload: dict | None = None):
        """Emit an event for other agents (Hermes, dashboard, etc.).

        Writes to Postgres (for audit/dashboard) AND publishes to OJ EventBus
        AND forwards to Hermes via A2A for instant alert dispatch.
        """
        full_payload = {"agent": self.name, **(payload or {})}
        bind_context_from_payload(full_payload)
        record_event_emitted(self.name, event_type)
        # 1. Postgres (audit trail + dashboard queries)
        await db.emit_event(event_type, full_payload)
        # 2. OJ EventBus (in-process subscribers)
        if self._bus is not None:
            try:
                from openjarvis.core.events import EventType
                self._bus.publish(EventType.A2A_REMOTE_EVENT, {
                    "event_type": event_type,
                    **full_payload,
                })
            except Exception:
                pass
        # 3. Forward to Hermes via A2A for instant alerts (non-blocking)
        if self.name != "hermes":
            try:
                from shared.oj_bridge import forward_event_to_hermes
                forward_event_to_hermes(event_type, full_payload)
            except Exception:
                pass  # DB fallback catches it on Hermes's poll cycle

    def request_shutdown(self):
        """Signal the agent to stop accepting new work."""
        self._shutdown_requested = True
        self._running = False
        self._state = AgentState.TERMINATED

    def begin_work(self, work_id: str):
        """Track in-flight work so shutdown can wait for it to finish."""
        first_work = not self._active_work
        self._active_work.add(work_id)
        self._work_started_at[work_id] = time.perf_counter()
        set_active_work(self.name, len(self._active_work))
        self._active_work_drained.clear()
        # Start heartbeat on first active work item (Phase 14)
        if first_work:
            asyncio.ensure_future(self._start_heartbeat())

    def finish_work(self, work_id: str):
        """Mark in-flight work as finished."""
        self._active_work.discard(work_id)
        started_at = self._work_started_at.pop(work_id, None)
        if started_at is not None:
            work_kind = work_id.split(":", 1)[0]
            observe_work_duration(self.name, work_kind, time.perf_counter() - started_at)
        set_active_work(self.name, len(self._active_work))
        if not self._active_work:
            self._active_work_drained.set()
            # Stop heartbeat when all work drains (Phase 14)
            asyncio.ensure_future(self._stop_heartbeat())

    async def wait_for_work_drain(self, timeout: float | None = None) -> bool:
        """Wait for in-flight work to finish."""
        if not self._active_work:
            return True
        try:
            await asyncio.wait_for(
                self._active_work_drained.wait(),
                timeout=timeout or self._shutdown_timeout_seconds,
            )
            return True
        except TimeoutError:
            return False

    async def finalize_shutdown(self):
        """Mark agent inactive and close its DB resources."""
        try:
            await self._transition(AgentState.TERMINATED)
            record_agent_shutdown(self.name)
            await self.deregister()
        finally:
            await db.close_pool()
            self._stopped.set()

    async def wait_until_stopped(self):
        """Wait until start() has fully cleaned up."""
        await self._stopped.wait()

    async def requeue_stale_tasks(self) -> int:
        """Release tasks left running by a previous crashed process."""
        rows = await db.fetch_all(
            """UPDATE task_queue
               SET status = 'pending',
                   assigned_agent = NULL,
                   started_at = NULL,
                   error = 'requeued after daemon restart'
               WHERE status = 'running' AND assigned_agent = %s
               RETURNING id""",
            (self.name,),
        )
        count = len(rows)
        if count:
            self.logger.warning("Requeued %d stale running task(s) for %s", count, self.name)
        return count

    async def get_pending_tasks(self, task_type: str | None = None) -> list[dict]:
        """Get pending task candidates for processing.

        Note: concurrency safety is handled in claim_task(), not here.
        Two agents seeing the same row is fine — only one will successfully
        claim it via the atomic CTE in claim_task().
        """
        if task_type:
            return await db.fetch_all(
                """SELECT * FROM task_queue
                   WHERE (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))
                     AND task_type = %s
                   ORDER BY priority ASC, created_at ASC LIMIT 50""",
                (task_type,),
            )
        return await db.fetch_all(
            """SELECT * FROM task_queue
               WHERE status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3)
               ORDER BY priority ASC, created_at ASC LIMIT 50""",
        )

    async def claim_task(self, task_id: int) -> bool:
        """Claim a task (set status to running). Returns True if claimed."""
        # Session health auto-reset (Phase 12: FP-05)
        if _session_health_enabled() and self._working_memory is not None:
            if self._working_memory.needs_reset():
                self.logger.warning(
                    "Session auto-reset triggered for %s (tokens=%d, elapsed=%.0fs)",
                    self.name,
                    self._working_memory.total_tokens,
                    self._working_memory.elapsed_seconds,
                )
                await self._flush_session_health()
                self._working_memory.reset()
                await self.emit_event("session_auto_reset", {
                    "reason": "threshold_breach",
                    "tokens": self._working_memory.total_tokens,
                })

        # Recursion guard (Phase 12: FP-03)
        if _recursion_guard_enabled():
            max_depth = await db.get_config("max_task_depth", 5)
            if isinstance(max_depth, str):
                max_depth = int(max_depth)
            depth_row = await db.fetch_one(
                "SELECT depth FROM task_queue WHERE id = %s", (task_id,)
            )
            if depth_row and (depth_row.get("depth") or 0) > max_depth:
                self.logger.warning(
                    "Recursion guard: task %d rejected (depth=%d, max=%d)",
                    task_id, depth_row["depth"], max_depth,
                )
                await self.fail_task(
                    task_id,
                    f"recursion_guard: depth {depth_row['depth']} > max {max_depth}",
                )
                return False

        # Atomic checkout (Phase 12: FP-02)
        # CTE pattern: SELECT FOR UPDATE SKIP LOCKED + UPDATE in a single statement.
        # Both operations share the same transaction, so SKIP LOCKED is effective.
        if _atomic_checkout_enabled():
            row = await db.fetch_one(
                """WITH locked AS (
                       SELECT id FROM task_queue
                       WHERE id = %s
                         AND (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))
                       FOR UPDATE SKIP LOCKED
                   )
                   UPDATE task_queue
                      SET status = 'running', started_at = NOW(), assigned_agent = %s
                     FROM locked
                    WHERE task_queue.id = locked.id
                    RETURNING task_queue.id""",
                (task_id, self.name),
            )
        else:
            row = await db.fetch_one(
                """UPDATE task_queue
                   SET status = 'running', started_at = NOW(), assigned_agent = %s
                   WHERE id = %s
                     AND (status = 'pending' OR (status = 'failed' AND COALESCE(retry_count, 0) < 3))
                   RETURNING id""",
                (self.name, task_id),
            )
        if row is not None:
            record_task_claimed(self.name)
            await self._transition(AgentState.EXECUTING)
            if _session_health_enabled() and self._working_memory is not None:
                self._working_memory.record_state_transition()
        return row is not None

    async def complete_task(self, task_id: int):
        """Mark a task as completed."""
        record_task_completed(self.name)
        await self._transition(AgentState.IDLE)
        await db.execute(
            """UPDATE task_queue
               SET status = 'completed', completed_at = NOW(), assigned_agent = %s
               WHERE id = %s""",
            (self.name, task_id),
        )

    async def fail_task(self, task_id: int, error: str):
        """Mark a task as failed."""
        record_task_failed(self.name)
        await self._transition(AgentState.ERROR)
        if _session_health_enabled() and self._working_memory is not None:
            self._working_memory.record_error()
        row = await db.fetch_one(
            """UPDATE task_queue
               SET retry_count = COALESCE(retry_count, 0) + 1,
                   status = CASE
                       WHEN COALESCE(retry_count, 0) + 1 >= 3 THEN 'dead_letter'
                       ELSE 'failed'
                   END,
                   error = %s,
                   completed_at = NOW(),
                   assigned_agent = %s
               WHERE id = %s
               RETURNING status, retry_count""",
            (error, self.name, task_id),
        )
        if row and row.get("status") == "dead_letter":
            await db.emit_event(
                "urgent_alert",
                {
                    "sender": self.name,
                    "message": f"Task {task_id} moved to dead-letter queue after {row.get('retry_count', 0)} failures",
                    "task_id": task_id,
                },
            )
        # Recover from ERROR so next task can be claimed (ERROR->EXECUTING not in matrix)
        await self._transition(AgentState.IDLE)

    async def spawn_child_task(
        self,
        parent_task_id: int,
        task_type: str,
        payload: dict | None = None,
        priority: int = 5,
    ) -> int | None:
        """Insert a child task with depth = parent.depth + 1 (Phase 12: FP-03)."""
        parent = await db.fetch_one(
            "SELECT depth FROM task_queue WHERE id = %s", (parent_task_id,)
        )
        parent_depth = (parent.get("depth") or 0) if parent else 0
        return await db.insert_task(
            task_type=task_type,
            payload=payload,
            priority=priority,
            depth=parent_depth + 1,
            dedupe=False,
        )
