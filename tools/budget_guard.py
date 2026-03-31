"""Budget Guard — Reporting tool for monthly budget status + multi-scope policy evaluation.

NOTE: Budget ENFORCEMENT lives in shared/middleware.py:check_budget_for_llm_call.
This module is for reporting, can_spend checks, and policy evaluation.

Phase 13 additions:
- PolicyResult / BudgetDecision dataclasses
- evaluate_policies() — multi-scope budget policy evaluation (Pattern 7)
- _query_spend_for_policy() — scope-aware spend queries
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from shared.config import config
from shared.db import fetch_all, fetch_val, get_config

logger = logging.getLogger("perseus.tools.budget")


class BudgetGuard:
    def __init__(self):
        self.monthly_cap = Decimal(str(config.budget.monthly_cap))
        self.alert_threshold = config.budget.alert_threshold

    async def check_budget(self) -> dict:
        """Check current month's spending against budget."""
        return await get_month_spending(self.monthly_cap)

    async def can_spend(self, amount: float, category: str) -> dict:
        """Check if a spend is allowed."""
        spending = await get_month_spending(self.monthly_cap)
        remaining = Decimal(str(spending["remaining"]))
        amount_dec = Decimal(str(amount))

        if amount_dec > remaining:
            return {
                "allowed": False,
                "exceeded": True,
                "reason": f"${amount} exceeds remaining ${remaining}",
                "remaining": float(remaining),
            }

        return {
            "allowed": True,
            "exceeded": False,
            "remaining": float(remaining - amount_dec),
        }


async def get_month_spending(monthly_cap: Decimal | None = None) -> dict:
    """Get total spending for the current month (fiat + Conway crypto)."""
    if monthly_cap is None:
        # Check dashboard override first, fall back to .env config
        db_cap = await get_config("monthly_budget_cap", None)
        if db_cap is not None:
            monthly_cap = Decimal(str(db_cap))
        else:
            monthly_cap = Decimal(str(config.budget.monthly_cap))

    month = date.today().replace(day=1)

    total = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
        (month,),
    ) or 0

    total_dec = Decimal(str(total))

    # Include Conway (crypto) spending if enabled
    conway_spend = Decimal("0")
    if config.conway.enabled:
        try:
            conway_total = await fetch_val(
                """SELECT COALESCE(SUM(amount), 0) FROM conway_ledger
                   WHERE tx_type IN ('spend', 'compute_rental', 'inference', 'service')
                   AND created_at >= %s""",
                (month,),
            ) or 0
            conway_spend = Decimal(str(conway_total))
        except Exception:
            pass  # Table may not exist yet

    combined = total_dec + conway_spend
    remaining = monthly_cap - combined
    percent = float(combined / monthly_cap * 100) if monthly_cap > 0 else 0

    categories = await fetch_all(
        """SELECT category, SUM(amount) as amount FROM v_effective_budget_tracking
           WHERE month = %s GROUP BY category ORDER BY amount DESC""",
        (month,),
    )
    cat_list = [dict(c) for c in categories] if categories else []

    # Add Conway as a category if it has spending
    if conway_spend > 0:
        cat_list.append({"category": "conway_crypto", "amount": float(conway_spend)})

    return {
        "total_spent": float(combined),
        "remaining": float(remaining),
        "percent_used": round(percent, 1),
        "exceeded": remaining <= 0,
        "categories": cat_list,
        "fiat_spent": float(total_dec),
        "crypto_spent": float(conway_spend),
    }


def get_budget_report_sync() -> str:
    """Sync wrapper for budget report (for non-async contexts)."""
    import asyncio
    return asyncio.run(_budget_report())


async def _budget_report() -> str:
    from shared.db import init_pool
    await init_pool()
    return await get_budget_report()


async def get_budget_report() -> str:
    """Generate a formatted budget report."""
    cap = Decimal(str(config.budget.monthly_cap))
    spending = await get_month_spending(cap)
    month_name = date.today().strftime("%B %Y")

    lines = [
        f"BUDGET — {month_name}",
        f"├── Spent: ${spending['total_spent']:.2f} / ${cap}",
        f"├── Remaining: ${spending['remaining']:.2f}",
        f"└── Used: {spending['percent_used']}%",
    ]

    if spending["categories"]:
        lines.append("")
        lines.append("BREAKDOWN")
        for cat in spending["categories"]:
            lines.append(f"  ├── {cat['category']}: ${cat['amount']}")

    if spending["percent_used"] >= config.budget.alert_threshold * 100:
        lines.append("")
        lines.append("WARNING: Approaching monthly cap!")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 13: Multi-Scope Budget Policies (Paperclip Pattern 7)
# ---------------------------------------------------------------------------


@dataclass
class PolicyResult:
    """Result of evaluating a single budget policy."""
    policy_id: int
    scope_type: str       # 'company' | 'agent' | 'pipeline_stage'
    scope_value: str
    window_kind: str      # 'monthly' | 'lifetime'
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    percent_used: float
    warn_triggered: bool  # True when percent_used >= warn_percent
    hard_stop: bool       # True when percent_used >= 100 AND hard_stop_enabled


@dataclass
class BudgetDecision:
    """Aggregate result of evaluating all applicable policies."""
    allowed: bool
    reason: str
    policies_evaluated: list[PolicyResult] = field(default_factory=list)
    most_restrictive: PolicyResult | None = None
    warnings: list[str] = field(default_factory=list)


async def evaluate_policies(
    agent_id: str | None = None,
    pipeline_stage: str | None = None,
) -> BudgetDecision:
    """Evaluate ALL applicable budget policies for a given context.

    Checks company, agent, and pipeline_stage scopes.
    Most restrictive policy wins (lowest remaining_usd).
    Returns BudgetDecision with allowed=True/False.

    Feature flag: MULTI_SCOPE_BUDGET_ENABLED (default false).
    When disabled, falls back to legacy $800 check.
    """
    if os.environ.get("MULTI_SCOPE_BUDGET_ENABLED", "false").lower() not in ("true", "1", "yes"):
        # Legacy fallback — single company cap
        spending = await get_month_spending()
        allowed = not spending["exceeded"]
        return BudgetDecision(
            allowed=allowed,
            reason="" if allowed else f"Company cap exceeded: ${spending['total_spent']:.2f}/${spending['remaining']:.2f}",
            policies_evaluated=[],
            most_restrictive=None,
            warnings=[],
        )

    # Load all policies
    policies = await fetch_all(
        "SELECT * FROM budget_policies ORDER BY scope_type, scope_value"
    )
    if not policies:
        # No policies defined — allow (but warn)
        logger.warning("No budget policies defined — allowing by default")
        return BudgetDecision(
            allowed=True, reason="no_policies_defined",
            policies_evaluated=[], most_restrictive=None, warnings=["No budget policies defined"],
        )

    results: list[PolicyResult] = []
    warnings: list[str] = []

    for policy in policies:
        scope_type = policy["scope_type"]
        scope_value = policy["scope_value"]
        window_kind = policy["window_kind"]
        limit_usd = float(policy["limit_usd"])
        warn_percent = int(policy["warn_percent"])
        hard_stop_enabled = bool(policy["hard_stop_enabled"])

        # Skip inapplicable policies
        if scope_type == "agent" and agent_id != scope_value:
            continue
        if scope_type == "pipeline_stage" and pipeline_stage != scope_value:
            continue

        # Query spend for this policy's scope + window
        spent = await _query_spend_for_policy(scope_type, scope_value, window_kind)
        remaining = limit_usd - spent
        percent_used = (spent / limit_usd * 100) if limit_usd > 0 else 100.0

        warn_triggered = percent_used >= warn_percent
        hard_stop = percent_used >= 100 and hard_stop_enabled

        result = PolicyResult(
            policy_id=policy["policy_id"],
            scope_type=scope_type,
            scope_value=scope_value,
            window_kind=window_kind,
            limit_usd=limit_usd,
            spent_usd=spent,
            remaining_usd=max(0, remaining),
            percent_used=round(percent_used, 1),
            warn_triggered=warn_triggered,
            hard_stop=hard_stop,
        )
        results.append(result)

        if warn_triggered and not hard_stop:
            warnings.append(
                f"Budget warning: {scope_type}/{scope_value} at {percent_used:.1f}% "
                f"(${spent:.2f}/${limit_usd:.2f})"
            )

    # Emit warnings to Hermes via event bus
    if warnings:
        try:
            from shared.db import emit_event
            for warning_msg in warnings:
                await emit_event("budget_warning", {
                    "message": warning_msg,
                    "agent_id": agent_id,
                    "pipeline_stage": pipeline_stage,
                })
        except Exception as exc:
            logger.warning("Budget warning event emission failed: %s", exc)

    # Most restrictive wins (lowest remaining)
    most_restrictive = min(results, key=lambda r: r.remaining_usd) if results else None

    # Any hard stop triggers rejection
    hard_stopped = [r for r in results if r.hard_stop]
    if hard_stopped:
        blocker = hard_stopped[0]
        return BudgetDecision(
            allowed=False,
            reason=f"Hard stop: {blocker.scope_type}/{blocker.scope_value} "
                   f"at {blocker.percent_used:.1f}% (${blocker.spent_usd:.2f}/${blocker.limit_usd:.2f})",
            policies_evaluated=results,
            most_restrictive=most_restrictive,
            warnings=warnings,
        )

    return BudgetDecision(
        allowed=True,
        reason="all_policies_within_limits",
        policies_evaluated=results,
        most_restrictive=most_restrictive,
        warnings=warnings,
    )


async def _query_spend_for_policy(scope_type: str, scope_value: str, window_kind: str) -> float:
    """Query total spend for a policy scope + window from cost_events + budget_tracking.

    Uses cost_events when PER_CALL_COST_EVENTS_ENABLED, otherwise falls back to budget_tracking.
    All SQL uses parameterized queries (AEGIS compliant — no f-string composition).
    """
    # Try cost_events first (richer data), fall back to budget_tracking
    use_cost_events = os.environ.get(
        "PER_CALL_COST_EVENTS_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    # Build query using separate parameterized paths (NO f-string SQL — AEGIS requirement)
    scope_params: tuple = ()

    if use_cost_events:
        if scope_type == "company" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)"
        elif scope_type == "company":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events"
        elif scope_type == "agent" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND agent_id = %s"
            scope_params = (scope_value,)
        elif scope_type == "agent":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE agent_id = %s"
            scope_params = (scope_value,)
        elif scope_type == "pipeline_stage" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND task_type = %s"
            scope_params = (scope_value,)
        elif scope_type == "pipeline_stage":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE task_type = %s"
            scope_params = (scope_value,)
        else:
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events"
    else:
        # Legacy: budget_tracking + llm_metrics combined (separate query per scope)
        if scope_type == "company" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)"
        elif scope_type == "company":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics"
        elif scope_type == "agent" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND daemon = %s"
            scope_params = (scope_value,)
        elif scope_type == "agent":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE daemon = %s"
            scope_params = (scope_value,)
        elif scope_type == "pipeline_stage" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(amount), 0) FROM budget_tracking WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND pipeline_stage = %s"
            scope_params = (scope_value,)
        elif scope_type == "pipeline_stage":
            query = "SELECT COALESCE(SUM(amount), 0) FROM budget_tracking WHERE pipeline_stage = %s"
            scope_params = (scope_value,)
        else:
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics"

    total = await fetch_val(query, scope_params) if scope_params else await fetch_val(query)
    return float(total or 0)
