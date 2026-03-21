"""
Base class for all Perseus agents.
Every agent (Titan, Hermes, OpenClaw, future agents) implements this interface
and registers with Perseus.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from shared import db


class AgentBase(ABC):
    """Base class for Perseus agents."""

    name: str = "unnamed"
    description: str = ""

    def __init__(self):
        self.logger = logging.getLogger(f"perseus.{self.name}")
        self._running = False

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
        """Register this agent with Perseus via DB."""
        await db.execute(
            """INSERT INTO agent_registry (name, description, status)
               VALUES (%s, %s, 'active')
               ON CONFLICT (name) DO UPDATE SET status = 'active', updated_at = NOW()""",
            (self.name, self.description),
        )
        self.logger.info(f"Agent '{self.name}' registered with Perseus")

    async def deregister(self):
        """Mark agent as inactive."""
        await db.execute(
            "UPDATE agent_registry SET status = 'inactive', updated_at = NOW() WHERE name = %s",
            (self.name,),
        )

    async def emit_event(self, event_type: str, payload: dict = None):
        """Emit an event for other agents (Hermes, dashboard, etc.)."""
        await db.emit_event(event_type, {"agent": self.name, **(payload or {})})

    async def get_pending_tasks(self, task_type: str = None) -> list[dict]:
        """Get pending tasks from the queue, optionally filtered by type."""
        if task_type:
            return await db.fetch_all(
                """SELECT * FROM task_queue
                   WHERE status = 'pending' AND task_type = %s
                   ORDER BY priority ASC, created_at ASC LIMIT 50""",
                (task_type,),
            )
        return await db.fetch_all(
            """SELECT * FROM task_queue
               WHERE status = 'pending'
               ORDER BY priority ASC, created_at ASC LIMIT 50""",
        )

    async def claim_task(self, task_id: int) -> bool:
        """Claim a task (set status to running). Returns True if claimed."""
        row = await db.fetch_one(
            """UPDATE task_queue SET status = 'running', started_at = NOW()
               WHERE id = %s AND status = 'pending' RETURNING id""",
            (task_id,),
        )
        return row is not None

    async def complete_task(self, task_id: int):
        """Mark a task as completed."""
        await db.execute(
            "UPDATE task_queue SET status = 'completed', completed_at = NOW() WHERE id = %s",
            (task_id,),
        )

    async def fail_task(self, task_id: int, error: str):
        """Mark a task as failed."""
        await db.execute(
            "UPDATE task_queue SET status = 'failed', error = %s, completed_at = NOW() WHERE id = %s",
            (error, task_id),
        )
