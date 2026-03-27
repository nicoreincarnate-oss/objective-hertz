"""ReviewGate — First N actions of a type require human approval.

Ported from Perseus (objective-hertz/titan/review_mode.py) and generalized
for any action type.  Perseus's version is tightly coupled to email/proposal
sending via Postgres.  This version is generic: it tracks approval counts
in SQLite and publishes events for the approval flow.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)

_CREATE_TABLES = """\
CREATE TABLE IF NOT EXISTS review_gate_config (
    action_type   TEXT PRIMARY KEY,
    required_n    INTEGER NOT NULL DEFAULT 10,
    approved_n    INTEGER NOT NULL DEFAULT 0,
    autonomous    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type   TEXT NOT NULL,
    agent         TEXT NOT NULL DEFAULT '',
    content       TEXT NOT NULL DEFAULT '',
    context_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',
    reviewer_notes TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    reviewed_at   REAL
);
"""


@dataclass(slots=True)
class ReviewItem:
    """An item waiting for review."""

    id: int
    action_type: str
    agent: str
    content: str
    context: dict[str, Any]
    status: str
    reviewer_notes: str
    created_at: float
    reviewed_at: float | None = None


class ReviewGate:
    """Controls which actions need human approval.

    For each action_type, the first N executions require approval.
    After N successful approvals, the action becomes autonomous.

    Usage::

        gate = ReviewGate(db_path="review.db", bus=bus)
        gate.configure("email_send", required_n=10)

        if gate.needs_review("email_send"):
            gate.submit_for_review("email_send", content="...", agent="titan")
            # Wait for approval via Telegram/CLI
        else:
            # Execute directly
            ...
    """

    def __init__(
        self,
        db_path: str,
        bus: EventBus | None = None,
    ) -> None:
        self._db_path = db_path
        self._bus = bus
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_TABLES)

    def configure(self, action_type: str, required_n: int = 10) -> None:
        """Set how many approvals an action type needs before going autonomous."""
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO review_gate_config "
            "(action_type, required_n, approved_n, autonomous, created_at, updated_at) "
            "VALUES (?, ?, COALESCE((SELECT approved_n FROM review_gate_config WHERE action_type = ?), 0), "
            "COALESCE((SELECT autonomous FROM review_gate_config WHERE action_type = ?), 0), ?, ?)",
            (action_type, required_n, action_type, action_type, now, now),
        )
        self._conn.commit()

    def needs_review(self, action_type: str) -> bool:
        """Check if an action type still requires human review."""
        row = self._conn.execute(
            "SELECT autonomous, approved_n, required_n FROM review_gate_config WHERE action_type = ?",
            (action_type,),
        ).fetchone()
        if not row:
            return False  # Not configured → no review needed
        return not row["autonomous"]

    def submit_for_review(
        self,
        action_type: str,
        content: str,
        agent: str = "",
        context: dict[str, Any] | None = None,
    ) -> int:
        """Submit an action for human review. Returns review item ID."""
        now = time.time()
        cursor = self._conn.execute(
            "INSERT INTO review_queue (action_type, agent, content, context_json, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (action_type, agent, content, json.dumps(context or {}), now),
        )
        self._conn.commit()
        review_id = cursor.lastrowid

        if self._bus:
            self._bus.publish(EventType.REVIEW_GATE_PENDING, {
                "review_id": review_id,
                "action_type": action_type,
                "agent": agent,
            })

        logger.info("Submitted for review: %s (id=%d, agent=%s)", action_type, review_id, agent)
        return review_id

    def approve(self, review_id: int, notes: str = "") -> bool:
        """Approve a review item. Increments approved count; may trigger autonomy."""
        row = self._conn.execute(
            "SELECT * FROM review_queue WHERE id = ? AND status = 'pending'",
            (review_id,),
        ).fetchone()
        if not row:
            return False

        now = time.time()
        self._conn.execute(
            "UPDATE review_queue SET status = 'approved', reviewer_notes = ?, reviewed_at = ? WHERE id = ?",
            (notes, now, review_id),
        )

        # Increment approved count
        self._conn.execute(
            "UPDATE review_gate_config SET approved_n = approved_n + 1, updated_at = ? WHERE action_type = ?",
            (now, row["action_type"]),
        )

        # Check if we've hit the threshold for autonomy
        config_row = self._conn.execute(
            "SELECT approved_n, required_n FROM review_gate_config WHERE action_type = ?",
            (row["action_type"],),
        ).fetchone()
        if config_row and config_row["approved_n"] >= config_row["required_n"]:
            self._conn.execute(
                "UPDATE review_gate_config SET autonomous = 1, updated_at = ? WHERE action_type = ?",
                (now, row["action_type"]),
            )
            logger.info(
                "Action '%s' is now AUTONOMOUS after %d approvals",
                row["action_type"], config_row["approved_n"],
            )

        self._conn.commit()

        if self._bus:
            self._bus.publish(EventType.REVIEW_GATE_APPROVED, {
                "review_id": review_id,
                "action_type": row["action_type"],
                "agent": row["agent"],
            })

        return True

    def reject(self, review_id: int, notes: str = "") -> bool:
        """Reject a review item."""
        now = time.time()
        result = self._conn.execute(
            "UPDATE review_queue SET status = 'rejected', reviewer_notes = ?, reviewed_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (notes, now, review_id),
        )
        self._conn.commit()
        return result.rowcount > 0

    def get_pending(self, action_type: str = "") -> list[ReviewItem]:
        """Get all pending review items, optionally filtered by action type."""
        if action_type:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status = 'pending' AND action_type = ? ORDER BY created_at ASC",
                (action_type,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status = 'pending' ORDER BY created_at ASC",
            ).fetchall()

        return [
            ReviewItem(
                id=r["id"],
                action_type=r["action_type"],
                agent=r["agent"],
                content=r["content"],
                context=json.loads(r["context_json"]),
                status=r["status"],
                reviewer_notes=r["reviewer_notes"],
                created_at=r["created_at"],
                reviewed_at=r["reviewed_at"],
            )
            for r in rows
        ]

    def status(self) -> list[dict[str, Any]]:
        """Get status of all configured action types."""
        rows = self._conn.execute(
            "SELECT * FROM review_gate_config ORDER BY action_type",
        ).fetchall()
        return [
            {
                "action_type": r["action_type"],
                "required_n": r["required_n"],
                "approved_n": r["approved_n"],
                "autonomous": bool(r["autonomous"]),
                "remaining": max(0, r["required_n"] - r["approved_n"]),
            }
            for r in rows
        ]


__all__ = ["ReviewGate", "ReviewItem"]
