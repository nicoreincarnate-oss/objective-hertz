"""DecisionAudit — Logs every autonomous decision with reasoning and outcome.

Ported from Perseus (objective-hertz/shared/comms.py record_decision /
record_decision_outcome).  Adapted to use SQLite (consistent with OpenJarvis's
AgentManager) and publishes events on the EventBus.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS agent_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent           TEXT NOT NULL,
    decision_type   TEXT NOT NULL,
    context_json    TEXT NOT NULL DEFAULT '{}',
    decision_json   TEXT NOT NULL DEFAULT '{}',
    reasoning       TEXT NOT NULL DEFAULT '',
    outcome_json    TEXT,
    created_at      REAL NOT NULL,
    outcome_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_decisions_agent ON agent_decisions(agent);
CREATE INDEX IF NOT EXISTS idx_decisions_type ON agent_decisions(decision_type);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON agent_decisions(created_at DESC);
"""


class DecisionAudit:
    """Records and queries autonomous agent decisions.

    Every significant autonomous decision is logged with:
    - Who made it (agent)
    - What type of decision (decision_type)
    - What they saw (context)
    - What they decided (decision)
    - Why (reasoning)
    - What happened (outcome, recorded later)

    This enables:
    - Auditability (what did the system do and why)
    - Cross-agent visibility (any agent can see others' decisions)
    - Closed-loop learning (compare decisions to outcomes)
    """

    def __init__(
        self,
        db_path: str,
        bus: Optional[EventBus] = None,
    ) -> None:
        self._db_path = db_path
        self._bus = bus
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_TABLE)

    def record_decision(
        self,
        agent: str,
        decision_type: str,
        context: Dict[str, Any],
        decision: Dict[str, Any],
        reasoning: str = "",
    ) -> int:
        """Record an autonomous decision. Returns decision ID."""
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO agent_decisions "
                "(agent, decision_type, context_json, decision_json, reasoning, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    agent,
                    decision_type,
                    json.dumps(context, default=str),
                    json.dumps(decision, default=str),
                    reasoning,
                    now,
                ),
            )
            self._conn.commit()
            decision_id = cursor.lastrowid

        if self._bus:
            self._bus.publish(EventType.DECISION_RECORDED, {
                "decision_id": decision_id,
                "agent": agent,
                "decision_type": decision_type,
                "reasoning": reasoning[:200],
            })

        logger.debug(
            "Decision recorded: id=%d agent=%s type=%s",
            decision_id, agent, decision_type,
        )
        return decision_id

    def record_outcome(
        self,
        decision_id: int,
        outcome: Dict[str, Any],
    ) -> None:
        """Update a decision with its observed outcome."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE agent_decisions SET outcome_json = ?, outcome_at = ? WHERE id = ?",
                (json.dumps(outcome, default=str), now, decision_id),
            )
            self._conn.commit()

    def get_recent(
        self,
        agent: str = "",
        decision_type: str = "",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Get recent decisions, optionally filtered."""
        conditions = []
        params: list = []

        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if decision_type:
            conditions.append("decision_type = ?")
            params.append(decision_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM agent_decisions {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()

        return [
            {
                "id": r["id"],
                "agent": r["agent"],
                "decision_type": r["decision_type"],
                "context": json.loads(r["context_json"]),
                "decision": json.loads(r["decision_json"]),
                "reasoning": r["reasoning"],
                "outcome": json.loads(r["outcome_json"]) if r["outcome_json"] else None,
                "created_at": r["created_at"],
                "outcome_at": r["outcome_at"],
            }
            for r in rows
        ]

    def get_by_id(self, decision_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific decision by ID."""
        row = self._conn.execute(
            "SELECT * FROM agent_decisions WHERE id = ?", (decision_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "agent": row["agent"],
            "decision_type": row["decision_type"],
            "context": json.loads(row["context_json"]),
            "decision": json.loads(row["decision_json"]),
            "reasoning": row["reasoning"],
            "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            "created_at": row["created_at"],
            "outcome_at": row["outcome_at"],
        }

    def count(self, agent: str = "", decision_type: str = "") -> int:
        """Count decisions, optionally filtered."""
        conditions = []
        params: list = []
        if agent:
            conditions.append("agent = ?")
            params.append(agent)
        if decision_type:
            conditions.append("decision_type = ?")
            params.append(decision_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM agent_decisions {where}", params,
        ).fetchone()
        return row["cnt"] if row else 0


__all__ = ["DecisionAudit"]
