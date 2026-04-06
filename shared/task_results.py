"""Re-entrant task execution result types.

When a task handler cannot complete in a single invocation, it returns
NEEDS_MORE_WORK with an updated payload. The task is re-queued with
depth+1. Hard limit prevents infinite loops.

Feature flag: ANATOMY_TASK_RESILIENCE
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_REENTRY_DEPTH = 10


@dataclass
class TaskResult:
    """Result from a task handler."""

    status: str  # "completed", "failed", "needs_more_work"
    payload: dict[str, Any] | None = None
    error: str | None = None
    requeue_priority: int | None = None  # Override priority on re-queue

    @property
    def needs_requeue(self) -> bool:
        return self.status == "needs_more_work"

    @staticmethod
    def completed(payload: dict | None = None) -> TaskResult:
        return TaskResult(status="completed", payload=payload)

    @staticmethod
    def failed(error: str, payload: dict | None = None) -> TaskResult:
        return TaskResult(status="failed", error=error, payload=payload)

    @staticmethod
    def needs_more_work(payload: dict, priority: int | None = None) -> TaskResult:
        return TaskResult(status="needs_more_work", payload=payload, requeue_priority=priority)

    def to_sentinel_dict(self) -> dict[str, Any]:
        """Convert to the sentinel dict format used by shared.db.maybe_requeue_task.

        This bridges the TaskResult API with the existing REENTRANT_SENTINEL
        protocol in shared.db.
        """
        if self.needs_requeue:
            return {
                "needs_more_work": True,
                "updated_payload": self.payload or {},
            }
        return {"status": self.status, "payload": self.payload, "error": self.error}
