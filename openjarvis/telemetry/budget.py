"""BudgetGuard — Enforces monthly spending cap across vassals and local inference.

Ported from Perseus (objective-hertz/tools/budget_guard.py) and adapted to
OpenJarvis's telemetry system.  Reads cost data from the SQLite telemetry
store (TelemetryRecord.cost_usd) and publishes EventBus alerts when
thresholds are crossed.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BudgetConfig:
    """Budget configuration — mirrors Perseus budget settings."""

    monthly_cap: float = 800.0
    alert_threshold: float = 0.8       # 80% → warning
    hard_cap_action: str = "pause"     # "pause" | "downgrade" | "alert_only"
    categories: dict[str, float] = field(default_factory=dict)  # per-category caps


class BudgetGuard:
    """Monitors spending and enforces budget caps.

    Can read from:
    - OpenJarvis SQLite telemetry store (local inference costs)
    - Vassal-reported spend (via A2A, stored in cost_records)

    Publishes events:
    - BUDGET_WARNING at alert_threshold
    - BUDGET_EXCEEDED at 100%
    - BUDGET_OK when spend drops back below cap
    """

    def __init__(
        self,
        config: BudgetConfig,
        bus: EventBus | None = None,
        telemetry_db_path: str | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._db_path = telemetry_db_path
        self._last_status: str = "ok"  # "ok" | "warning" | "exceeded"
        self._cost_records: list[dict[str, Any]] = []

    @property
    def config(self) -> BudgetConfig:
        return self._config

    def record_cost(
        self,
        amount: float,
        category: str = "inference",
        source: str = "local",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a cost event (for vassal-reported spend or manual tracking)."""
        self._cost_records.append({
            "amount": amount,
            "category": category,
            "source": source,
            "timestamp": time.time(),
            "month": date.today().replace(day=1).isoformat(),
            "metadata": metadata or {},
        })

    def check_budget(self) -> dict[str, Any]:
        """Check current month's spending against budget.

        Returns dict with: total_spent, remaining, percent_used, exceeded,
        status, categories.
        """
        cap = Decimal(str(self._config.monthly_cap))
        current_month = date.today().replace(day=1).isoformat()

        # Sum from cost records
        total = Decimal("0")
        category_totals: dict[str, Decimal] = {}

        for rec in self._cost_records:
            if rec["month"] == current_month:
                amt = Decimal(str(rec["amount"]))
                total += amt
                cat = rec.get("category", "other")
                category_totals[cat] = category_totals.get(cat, Decimal("0")) + amt

        # Also read from telemetry DB if available
        if self._db_path:
            try:
                db_total, db_categories = self._read_telemetry_costs(current_month)
                total += db_total
                for cat, amt in db_categories.items():
                    category_totals[cat] = category_totals.get(cat, Decimal("0")) + amt
            except Exception as exc:
                logger.debug("Failed to read telemetry costs: %s", exc)

        remaining = cap - total
        percent = float(total / cap * 100) if cap > 0 else 0.0

        # Determine status and publish events
        exceeded = remaining <= 0
        if exceeded:
            new_status = "exceeded"
        elif percent >= self._config.alert_threshold * 100:
            new_status = "warning"
        else:
            new_status = "ok"

        # Publish events on status transitions
        if self._bus and new_status != self._last_status:
            if new_status == "exceeded":
                self._bus.publish(EventType.BUDGET_EXCEEDED, {
                    "total_spent": float(total),
                    "cap": float(cap),
                    "percent_used": round(percent, 1),
                    "action": self._config.hard_cap_action,
                })
            elif new_status == "warning":
                self._bus.publish(EventType.BUDGET_WARNING, {
                    "total_spent": float(total),
                    "cap": float(cap),
                    "percent_used": round(percent, 1),
                })
            elif new_status == "ok" and self._last_status in ("exceeded", "warning"):
                self._bus.publish(EventType.BUDGET_OK, {
                    "total_spent": float(total),
                    "cap": float(cap),
                    "percent_used": round(percent, 1),
                })

        self._last_status = new_status

        categories = [
            {"category": cat, "amount": float(amt)}
            for cat, amt in sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "total_spent": float(total),
            "remaining": float(remaining),
            "percent_used": round(percent, 1),
            "exceeded": exceeded,
            "status": new_status,
            "cap": float(cap),
            "categories": categories,
        }

    def can_spend(self, amount: float, category: str = "inference") -> dict[str, Any]:
        """Check if a spend is allowed under current budget."""
        status = self.check_budget()
        remaining = Decimal(str(status["remaining"]))
        amount_dec = Decimal(str(amount))

        # Check category-level cap if configured
        cat_cap = self._config.categories.get(category)
        if cat_cap is not None:
            cat_spent = Decimal("0")
            for cat_info in status["categories"]:
                if cat_info["category"] == category:
                    cat_spent = Decimal(str(cat_info["amount"]))
                    break
            cat_remaining = Decimal(str(cat_cap)) - cat_spent
            if amount_dec > cat_remaining:
                return {
                    "allowed": False,
                    "reason": f"${amount} exceeds {category} cap (${cat_remaining} remaining)",
                    "remaining": float(remaining),
                    "category_remaining": float(cat_remaining),
                }

        if amount_dec > remaining:
            return {
                "allowed": False,
                "reason": f"${amount} exceeds total remaining ${remaining}",
                "remaining": float(remaining),
            }

        return {
            "allowed": True,
            "remaining": float(remaining - amount_dec),
        }

    def get_model_tier(self) -> str:
        """Get recommended model tier based on budget status.

        Returns: "full" | "downgraded" | "local_only"
        """
        status = self.check_budget()
        percent = status["percent_used"]

        if percent >= 100:
            return "local_only"
        elif percent >= self._config.alert_threshold * 100:
            return "downgraded"
        return "full"

    def format_report(self) -> str:
        """Generate a formatted budget report string."""
        status = self.check_budget()
        month_name = date.today().strftime("%B %Y")

        lines = [
            f"BUDGET — {month_name}",
            f"├── Spent: ${status['total_spent']:.2f} / ${status['cap']:.2f}",
            f"├── Remaining: ${status['remaining']:.2f}",
            f"├── Used: {status['percent_used']}%",
            f"└── Status: {status['status']}",
        ]

        if status["categories"]:
            lines.append("")
            lines.append("BREAKDOWN")
            for cat in status["categories"]:
                lines.append(f"  ├── {cat['category']}: ${cat['amount']:.2f}")

        tier = self.get_model_tier()
        if tier != "full":
            lines.append("")
            lines.append(f"MODEL TIER: {tier}")

        return "\n".join(lines)

    def _read_telemetry_costs(self, month_iso: str) -> tuple[Decimal, dict[str, Decimal]]:
        """Read costs from OpenJarvis telemetry SQLite store."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            # Parse month to get timestamp range
            month_date = datetime.fromisoformat(month_iso)
            start_ts = month_date.timestamp()
            if month_date.month == 12:
                end_date = month_date.replace(year=month_date.year + 1, month=1)
            else:
                end_date = month_date.replace(month=month_date.month + 1)
            end_ts = end_date.timestamp()

            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total FROM telemetry "
                "WHERE timestamp >= ? AND timestamp < ?",
                (start_ts, end_ts),
            ).fetchone()
            total = Decimal(str(row["total"])) if row else Decimal("0")

            rows = conn.execute(
                "SELECT engine, SUM(cost_usd) as amount FROM telemetry "
                "WHERE timestamp >= ? AND timestamp < ? "
                "GROUP BY engine ORDER BY amount DESC",
                (start_ts, end_ts),
            ).fetchall()
            categories = {r["engine"]: Decimal(str(r["amount"])) for r in rows}

            return total, categories
        finally:
            conn.close()


__all__ = ["BudgetConfig", "BudgetGuard"]
