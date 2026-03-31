"""Per-call cost event recording — Paperclip Pattern 8.

Every LLM call emits a CostEvent to the cost_events table.
Includes task_id from observability context for attribution.
Feature flag: PER_CALL_COST_EVENTS_ENABLED (env-based, default false).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.cost_events")


def _cost_events_enabled() -> bool:
    return os.environ.get("PER_CALL_COST_EVENTS_ENABLED", "false").lower() in ("true", "1", "yes")


@dataclass
class CostEvent:
    """Single LLM call cost record."""
    agent_id: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    task_id: str | None = None
    task_type: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)


async def emit_cost_event(event: CostEvent) -> None:
    """Write a CostEvent to the cost_events table.

    Fire-and-forget: logs warning on failure, never raises.
    Respects PER_CALL_COST_EVENTS_ENABLED feature flag.
    """
    if not _cost_events_enabled():
        return

    try:
        from shared.db import execute

        await execute(
            """INSERT INTO cost_events
               (event_id, agent_id, task_id, task_type, model,
                tokens_in, tokens_out, cached_tokens, cost_usd, latency_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                event.event_id,
                event.agent_id,
                event.task_id,
                event.task_type,
                event.model,
                event.tokens_in,
                event.tokens_out,
                event.cached_tokens,
                round(event.cost_usd, 6),
                event.latency_ms,
            ),
        )
    except Exception as exc:
        logger.warning("Cost event emission failed (non-fatal): %s", exc)


async def get_agent_spend(agent_id: str, window_sql: str = "30 days") -> float:
    """Sum cost_usd for an agent within a time window.

    Returns 0.0 on error (fail safe for reads — writes are what must fail closed).
    """
    try:
        from shared.db import fetch_val

        total = await fetch_val(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events "
            "WHERE agent_id = %s AND created_at >= NOW() - INTERVAL %s",
            (agent_id, window_sql),
        )
        return float(total or 0)
    except Exception as exc:
        logger.warning("Agent spend query failed: %s", exc)
        return 0.0


async def get_total_spend_current_month() -> float:
    """Sum cost_usd across all agents for the current calendar month.

    Returns 0.0 on error.
    """
    try:
        from shared.db import fetch_val

        total = await fetch_val(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events "
            "WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)"
        )
        return float(total or 0)
    except Exception as exc:
        logger.warning("Monthly spend query failed: %s", exc)
        return 0.0


async def get_average_cost_by_task_type(task_type: str, lookback_days: int = 30) -> float | None:
    """Lookup average cost for a task_type from historical cost_events.

    Used by Pattern 6 (pre-execution budget gate) for cost estimation.
    Returns None if no data exists (caller decides fallback).
    """
    try:
        from shared.db import fetch_val

        avg = await fetch_val(
            "SELECT AVG(cost_usd) FROM cost_events "
            "WHERE task_type = %s AND created_at >= NOW() - INTERVAL %s",
            (task_type, f"{lookback_days} days"),
        )
        return float(avg) if avg is not None else None
    except Exception as exc:
        logger.warning("Average cost lookup failed: %s", exc)
        return None
