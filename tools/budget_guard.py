"""Budget Guard — Enforces monthly budget cap for Perseus operations."""

import logging
from datetime import date
from decimal import Decimal

from shared.config import config
from shared.db import fetch_one, fetch_all, execute, fetch_val

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
    """Get total spending for the current month."""
    if monthly_cap is None:
        monthly_cap = Decimal(str(config.budget.monthly_cap))

    month = date.today().replace(day=1)

    total = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
        (month,),
    ) or 0

    total_dec = Decimal(str(total))
    remaining = monthly_cap - total_dec
    percent = float(total_dec / monthly_cap * 100) if monthly_cap > 0 else 0

    categories = await fetch_all(
        """SELECT category, SUM(amount) as amount FROM v_effective_budget_tracking
           WHERE month = %s GROUP BY category ORDER BY amount DESC""",
        (month,),
    )

    return {
        "total_spent": float(total_dec),
        "remaining": float(remaining),
        "percent_used": round(percent, 1),
        "exceeded": remaining <= 0,
        "categories": [dict(c) for c in categories] if categories else [],
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
