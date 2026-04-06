"""Per-call cost event recording — Paperclip Pattern 8 + Phase 31 BATS.

Every LLM call emits a CostEvent to the cost_events table.
Includes task_id from observability context for attribution.
Feature flag: PER_CALL_COST_EVENTS_ENABLED (env-based, default false).

Phase 31 additions:
- UnifiedBudget: tracks combined token + tool costs with regime awareness (BATS)
- CostEvent extended with tool_name, budget_regime, task_id linkage
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.cost_events")


def _cost_events_enabled() -> bool:
    return os.environ.get("PER_CALL_COST_EVENTS_ENABLED", "false").lower() in ("true", "1", "yes")


def _bats_enabled() -> bool:
    return os.environ.get("BATS_ADAPTIVE_BUDGET", "true").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Tool pricing defaults (Phase 31 — BATS)
# ---------------------------------------------------------------------------

DEFAULT_TOOL_PRICES: dict[str, float] = {
    # Internal tools (compute proxy)
    "shell_exec": 0.001,
    "file_read": 0.001,
    "file_write": 0.001,
    "git": 0.001,
    # External API tools
    "web_search": 0.01,
    "firecrawl": 0.01,
    "crawl4ai": 0.01,
    "searxng": 0.01,
    # No-cost tools
    "think": 0.0,
    "calculator": 0.0,
}


# ---------------------------------------------------------------------------
# UnifiedBudget — Phase 31 BATS budget awareness
# ---------------------------------------------------------------------------

@dataclass
class UnifiedBudget:
    """Tracks combined token + tool costs with regime awareness (BATS).

    Budget regimes:
    - HIGH (>=70% remaining): explore freely
    - MEDIUM (30-70%): verify before new tools
    - LOW (10-30%): wrap up, respond with current knowledge
    - CRITICAL (<10%): respond immediately with best available answer
    """
    # Token costs
    token_budget_usd: float          # allocated for this task
    token_spent_usd: float = 0.0
    # Tool costs
    tool_budgets: dict[str, int] = field(default_factory=dict)   # tool -> max calls
    tool_used: dict[str, int] = field(default_factory=dict)      # tool -> calls made
    tool_prices: dict[str, float] = field(default_factory=dict)  # tool -> $/call

    @property
    def total_budget(self) -> float:
        return self.token_budget_usd + sum(
            self.tool_budgets.get(t, 0) * self.tool_prices.get(t, 0)
            for t in self.tool_budgets
        )

    @property
    def total_spent(self) -> float:
        return self.token_spent_usd + sum(
            self.tool_used.get(t, 0) * self.tool_prices.get(t, 0)
            for t in self.tool_used
        )

    @property
    def remaining_pct(self) -> float:
        tb = max(self.total_budget, 0.001)
        return max(0.0, (1 - self.total_spent / tb) * 100)

    @property
    def budget_regime(self) -> str:
        pct = self.remaining_pct
        if pct >= 70:
            return "HIGH"
        if pct >= 30:
            return "MEDIUM"
        if pct >= 10:
            return "LOW"
        return "CRITICAL"

    def record_token_spend(self, cost_usd: float) -> None:
        """Record token spend."""
        self.token_spent_usd += cost_usd

    def record_tool_use(self, tool_name: str) -> None:
        """Record a tool call, using default pricing if unknown."""
        self.tool_used[tool_name] = self.tool_used.get(tool_name, 0) + 1
        if tool_name not in self.tool_prices:
            self.tool_prices[tool_name] = DEFAULT_TOOL_PRICES.get(tool_name, 0.001)

    @property
    def regime_hint(self) -> str:
        """Return a behavioral hint string for the current budget regime."""
        regime = self.budget_regime
        if regime == "HIGH":
            return "explore freely"
        if regime == "MEDIUM":
            return "verify before new tools"
        if regime == "LOW":
            return "wrap up, respond with current knowledge"
        return "respond immediately with best available answer"

    def format_status(self) -> str:
        """Format a compact budget status message (capped at ~80 tokens)."""
        tool_count = sum(self.tool_used.values())
        tool_budget_total = sum(self.tool_budgets.values()) if self.tool_budgets else 0
        return (
            f"[BUDGET: {self.remaining_pct:.0f}% remaining | "
            f"regime={self.budget_regime} | "
            f"tokens=${self.token_spent_usd:.2f}/${self.token_budget_usd:.2f} | "
            f"tools={tool_count}/{tool_budget_total or '?'} | "
            f"hint={self.regime_hint}]"
        )


def create_default_budget(
    token_budget_usd: float = 1.0,
    tool_budget_per_tool: int = 10,
) -> UnifiedBudget:
    """Create a UnifiedBudget with sensible defaults."""
    tool_budgets = {name: tool_budget_per_tool for name in DEFAULT_TOOL_PRICES}
    return UnifiedBudget(
        token_budget_usd=token_budget_usd,
        tool_budgets=tool_budgets,
        tool_prices=dict(DEFAULT_TOOL_PRICES),
    )


# ---------------------------------------------------------------------------
# CostEvent — original + Phase 31 enhancements
# ---------------------------------------------------------------------------

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
    # Phase 31 — BATS enhancements
    tool_name: str | None = None         # which tool triggered this cost
    budget_regime: str | None = None     # regime at time of event


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
    except (OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Monthly spend query failed: %s", exc)
        return 0.0


async def get_weekly_agent_cost_report(lookback_days: int = 7) -> list[dict]:
    """Weekly per-agent token consumption report from cost_events table.

    Returns agents ranked by total spend, with breakdown by model tier.
    Identifies agents consuming disproportionate budget (>2x mean).

    Feature flag: ANATOMY_COST_DASHBOARD
    """
    if os.environ.get("ANATOMY_COST_DASHBOARD", "").lower() not in ("true", "1"):
        return []

    try:
        from shared.db import fetch_all

        rows = await fetch_all(
            """SELECT
                agent_id,
                model,
                COUNT(*) AS call_count,
                SUM(tokens_in) AS total_input_tokens,
                SUM(tokens_out) AS total_output_tokens,
                SUM(cached_tokens) AS total_cached_tokens,
                ROUND(SUM(cost_usd)::numeric, 4) AS total_cost,
                ROUND(AVG(latency_ms)::numeric, 0) AS avg_latency_ms,
                ROUND(AVG(cost_usd)::numeric, 6) AS avg_cost_per_call
            FROM cost_events
            WHERE created_at >= NOW() - INTERVAL %s
            GROUP BY agent_id, model
            ORDER BY total_cost DESC""",
            (f"{lookback_days} days",),
        )
        return [dict(r) for r in rows]
    except (OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Weekly cost report query failed: %s", exc)
        return []


async def get_agent_cost_summary(lookback_days: int = 7) -> dict:
    """High-level cost summary with disproportionate spend detection.

    Returns:
        {
            "total_spend": float,
            "agent_breakdown": [...],
            "disproportionate_agents": [...],  # agents spending >2x mean
            "cache_efficiency": float,  # cached_tokens / total_input_tokens
            "period_days": int,
        }
    """
    report = await get_weekly_agent_cost_report(lookback_days)
    if not report:
        return {
            "total_spend": 0,
            "agent_breakdown": [],
            "disproportionate_agents": [],
            "cache_efficiency": 0,
            "period_days": lookback_days,
        }

    # Aggregate per-agent (collapse model dimension)
    agent_totals: dict[str, float] = {}
    total_input = 0
    total_cached = 0
    for row in report:
        aid = row["agent_id"]
        agent_totals[aid] = agent_totals.get(aid, 0) + float(row["total_cost"])
        total_input += int(row.get("total_input_tokens") or 0)
        total_cached += int(row.get("total_cached_tokens") or 0)

    total_spend = sum(agent_totals.values())
    mean_spend = total_spend / len(agent_totals) if agent_totals else 0
    disproportionate = [aid for aid, cost in agent_totals.items() if cost > 2 * mean_spend]
    cache_efficiency = total_cached / total_input if total_input > 0 else 0

    return {
        "total_spend": round(total_spend, 4),
        "agent_breakdown": report,
        "disproportionate_agents": disproportionate,
        "cache_efficiency": round(cache_efficiency, 4),
        "period_days": lookback_days,
    }


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
    except (OSError, RuntimeError, ValueError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Average cost lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Phase 31: BATS budget decision logging
# ---------------------------------------------------------------------------

async def log_budget_decision(
    task_id: str | None,
    regime: str,
    token_spent: float,
    token_budget: float,
    tool_calls_made: int,
    tool_budget: int,
    constraint_triggered: str | None,
    action_taken: str,
) -> None:
    """Log a BATS budget decision to the budget_decisions table.

    Fire-and-forget: logs warning on failure, never raises.
    """
    if not _bats_enabled():
        return

    try:
        from shared.db import execute

        await execute(
            """INSERT INTO budget_decisions
               (task_id, regime, token_spent, token_budget,
                tool_calls_made, tool_budget, constraint_triggered, action_taken)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                task_id,
                regime,
                round(token_spent, 6),
                round(token_budget, 6),
                tool_calls_made,
                tool_budget,
                constraint_triggered,
                action_taken,
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Budget decision log failed (non-fatal): %s", exc)
