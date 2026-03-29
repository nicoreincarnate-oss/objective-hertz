"""
Base class for all Perseus agents.
Every agent (Titan, Hermes, OpenClaw, future agents) implements this interface
and registers with Perseus.
"""

from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from shared import db

if TYPE_CHECKING:
    from shared.daemon_memory import DaemonMemoryStore, MemoryCache, WorkingMemory


def _deerflow_memory_enabled() -> bool:
    """Check ENABLE_DEERFLOW_MEMORY feature flag."""
    return os.environ.get("ENABLE_DEERFLOW_MEMORY", "").lower() in ("true", "1")


class AgentBase(ABC):
    """Base class for Perseus agents."""

    name: str = "unnamed"
    description: str = ""

    def __init__(self):
        self.logger = logging.getLogger(f"perseus.{self.name}")
        self._running = False
        self._shutdown_requested = False
        self._active_work: set[str] = set()
        self._active_work_drained = asyncio.Event()
        self._active_work_drained.set()
        self._stopped = asyncio.Event()
        self._stopped.set()
        self._shutdown_timeout_seconds = 45
        # OJ EventBus integration
        try:
            from shared.oj_bridge import get_bus
            self._bus = get_bus()
        except Exception:
            self._bus = None

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

    async def register(self):
        """Register this agent with Perseus via DB, and optionally create a Conway wallet."""
        await db.execute(
            """INSERT INTO agent_registry (name, description, status)
               VALUES (%s, %s, 'active')
               ON CONFLICT (name) DO UPDATE SET status = 'active', updated_at = NOW()""",
            (self.name, self.description),
        )
        self.logger.info(f"Agent '{self.name}' registered")

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
        self._active_work_drained.clear()

    def finish_work(self, work_id: str):
        """Mark in-flight work as finished."""
        self._active_work.discard(work_id)
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
        return row is not None

    async def complete_task(self, task_id: int):
        """Mark a task as completed."""
        await db.execute(
            """UPDATE task_queue
               SET status = 'completed', completed_at = NOW(), assigned_agent = %s
               WHERE id = %s""",
            (self.name, task_id),
        )

    async def fail_task(self, task_id: int, error: str):
        """Mark a task as failed."""
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
