"""
Base class for all Perseus agents.
Every agent (Titan, Hermes, OpenClaw, future agents) implements this interface
and registers with Perseus.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod

from shared import db
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

    def begin_work(self, work_id: str):
        """Track in-flight work so shutdown can wait for it to finish."""
        self._active_work.add(work_id)
        self._work_started_at[work_id] = time.perf_counter()
        set_active_work(self.name, len(self._active_work))
        self._active_work_drained.clear()

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
        """Get pending tasks from the queue, optionally filtered by type."""
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
        return row is not None

    async def complete_task(self, task_id: int):
        """Mark a task as completed."""
        record_task_completed(self.name)
        await db.execute(
            """UPDATE task_queue
               SET status = 'completed', completed_at = NOW(), assigned_agent = %s
               WHERE id = %s""",
            (self.name, task_id),
        )

    async def fail_task(self, task_id: int, error: str):
        """Mark a task as failed."""
        record_task_failed(self.name)
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
