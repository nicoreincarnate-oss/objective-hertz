"""DeadLetterQueue — Quarantines tasks that fail repeatedly.

Ported from Perseus's agent_base.py pattern where tasks that fail 3+ times
are moved to a dead-letter table with alerts.  Uses SQLite consistent with
OpenJarvis's scheduler store.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)

_CREATE_TABLES = """\
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT NOT NULL,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    error_messages  TEXT NOT NULL DEFAULT '[]',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    source_agent    TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    resolved_at     REAL,
    status          TEXT NOT NULL DEFAULT 'quarantined'
);

CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);
CREATE INDEX IF NOT EXISTS idx_dlq_type ON dead_letter_queue(task_type);
"""

DEFAULT_MAX_RETRIES = 3


class DeadLetterQueue:
    """Quarantines tasks that have failed too many times.

    Usage::

        dlq = DeadLetterQueue(db_path="scheduler.db", bus=bus)

        # When a task fails:
        should_quarantine = dlq.record_failure(task_type, payload, error_msg, agent)
        if should_quarantine:
            # Task has been moved to dead-letter — don't retry
            ...

        # List quarantined tasks:
        items = dlq.list_quarantined()

        # Manually retry:
        dlq.retry(item_id)

        # Or discard:
        dlq.discard(item_id)
    """

    def __init__(
        self,
        db_path: str,
        bus: EventBus | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._db_path = db_path
        self._bus = bus
        self._max_retries = max_retries
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_TABLES)
        # In-memory retry counter: (task_type, payload_hash) -> (count, errors)
        self._failure_counts: dict[str, tuple[int, list[str]]] = {}

    def record_failure(
        self,
        task_type: str,
        payload: dict[str, Any],
        error: str,
        source_agent: str = "",
    ) -> bool:
        """Record a task failure.  Returns True if the task was quarantined.

        Tracks failures by (task_type + payload hash).  After max_retries
        consecutive failures, moves the task to the dead-letter table.
        """
        key = f"{task_type}:{hash(json.dumps(payload, sort_keys=True, default=str))}"

        count, errors = self._failure_counts.get(key, (0, []))
        count += 1
        errors.append(error)
        self._failure_counts[key] = (count, errors)

        if count >= self._max_retries:
            self._quarantine(task_type, payload, errors, source_agent)
            del self._failure_counts[key]
            return True

        return False

    def clear_failures(self, task_type: str, payload: dict[str, Any]) -> None:
        """Clear failure count for a task (call on success)."""
        key = f"{task_type}:{hash(json.dumps(payload, sort_keys=True, default=str))}"
        self._failure_counts.pop(key, None)

    def _quarantine(
        self,
        task_type: str,
        payload: dict[str, Any],
        errors: list[str],
        source_agent: str,
    ) -> int:
        """Move a task to the dead-letter queue."""
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO dead_letter_queue "
            "(task_type, payload_json, error_messages, retry_count, source_agent, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_type,
                json.dumps(payload, default=str),
                json.dumps(errors),
                len(errors),
                source_agent,
                now,
            ),
        )
        self._conn.commit()
        dlq_id = cursor.lastrowid

        logger.warning(
            "Task quarantined: type=%s agent=%s retries=%d id=%d",
            task_type, source_agent, len(errors), dlq_id,
        )

        if self._bus:
            self._bus.publish(EventType.SECURITY_ALERT, {
                "alert_type": "dead_letter",
                "dlq_id": dlq_id,
                "task_type": task_type,
                "source_agent": source_agent,
                "retry_count": len(errors),
                "last_error": errors[-1] if errors else "",
            })

        return dlq_id

    def list_quarantined(self, limit: int = 50) -> list[dict[str, Any]]:
        """List quarantined tasks."""
        rows = self._conn.execute(
            "SELECT * FROM dead_letter_queue WHERE status = 'quarantined' "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "task_type": r["task_type"],
                "payload": json.loads(r["payload_json"]),
                "errors": json.loads(r["error_messages"]),
                "retry_count": r["retry_count"],
                "source_agent": r["source_agent"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def retry(self, dlq_id: int) -> dict[str, Any] | None:
        """Mark a quarantined task for retry.  Returns the task info."""
        row = self._conn.execute(
            "SELECT * FROM dead_letter_queue WHERE id = ? AND status = 'quarantined'",
            (dlq_id,),
        ).fetchone()
        if not row:
            return None

        now = time.time()
        self._conn.execute(
            "UPDATE dead_letter_queue SET status = 'retrying', resolved_at = ? WHERE id = ?",
            (now, dlq_id),
        )
        self._conn.commit()

        return {
            "task_type": row["task_type"],
            "payload": json.loads(row["payload_json"]),
            "source_agent": row["source_agent"],
        }

    def discard(self, dlq_id: int) -> bool:
        """Discard a quarantined task (won't be retried)."""
        result = self._conn.execute(
            "UPDATE dead_letter_queue SET status = 'discarded', resolved_at = ? WHERE id = ?",
            (time.time(), dlq_id),
        )
        self._conn.commit()
        return result.rowcount > 0

    def count(self) -> int:
        """Count quarantined tasks."""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM dead_letter_queue WHERE status = 'quarantined'",
        ).fetchone()
        return row["cnt"] if row else 0


__all__ = ["DeadLetterQueue"]
