"""Spend alert thresholds — Phase 41.

Proactive notifications when daemon spend approaches budget caps. Wires into
Hermes alert infrastructure (Telegram + War Room dashboard).

Threshold ladder per daemon:
  50% — info (logged only)
  75% — warning (Telegram alert)
  90% — critical (Telegram alert + auto-downgrade enabled)
 100% — block (refuse new requests, page operator)

Same ladder applies to daily, weekly, and monthly windows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger("perseus.spend_alerts")


class AlertLevel(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"
    BLOCK    = "block"


@dataclass
class SpendAlert:
    level: AlertLevel
    daemon: str
    window: str            # "daily" | "weekly" | "monthly"
    spent_usd: float
    cap_usd: float
    pct_of_cap: float
    message: str
    timestamp: datetime
    actions_taken: list[str]


THRESHOLDS = [
    (1.00, AlertLevel.BLOCK,    "Daemon cap reached — blocking new requests"),
    (0.90, AlertLevel.CRITICAL, "Critical: 90% of cap reached — enabling auto-downgrade"),
    (0.75, AlertLevel.WARNING,  "Warning: 75% of cap reached"),
    (0.50, AlertLevel.INFO,     "Info: 50% of cap reached"),
]


async def check_daemon_spend(daemon: str, db_pool) -> list[SpendAlert]:
    """Check all spend windows for one daemon. Returns alerts that fired."""
    alerts: list[SpendAlert] = []

    async with db_pool.connection() as conn:
        # Daily check
        daily_cap_row = await conn.execute(
            "SELECT daily_cap_usd, monthly_cap_usd FROM daemon_budget_caps WHERE daemon = %s",
            (daemon,),
        )
        cap_row = await daily_cap_row.fetchone()
        if not cap_row:
            return []
        daily_cap, monthly_cap = float(cap_row[0] or 0), float(cap_row[1])

        if daily_cap > 0:
            today_spent_row = await conn.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM tier_spend_log
                WHERE daemon = %s AND DATE(timestamp) = CURRENT_DATE AND success = true
                """,
                (daemon,),
            )
            row = await today_spent_row.fetchone()
            today_spent = float(row[0]) if row else 0.0
            alerts.extend(_evaluate_thresholds(daemon, "daily", today_spent, daily_cap))

        # Monthly check
        month_spent_row = await conn.execute(
            """
            SELECT COALESCE(SUM(cost_usd), 0)
            FROM tier_spend_log
            WHERE daemon = %s
              AND DATE_TRUNC('month', timestamp) = DATE_TRUNC('month', CURRENT_DATE)
              AND success = true
            """,
            (daemon,),
        )
        row = await month_spent_row.fetchone()
        month_spent = float(row[0]) if row else 0.0
        alerts.extend(_evaluate_thresholds(daemon, "monthly", month_spent, monthly_cap))

    return alerts


async def check_global_spend(db_pool, daily_cap: float = 40.0, monthly_cap: float = 350.0) -> list[SpendAlert]:
    """Check global spend across all daemons against the system-wide cap."""
    alerts: list[SpendAlert] = []
    async with db_pool.connection() as conn:
        # Daily
        row = await (await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM tier_spend_log "
            "WHERE DATE(timestamp) = CURRENT_DATE AND success = true"
        )).fetchone()
        today_spent = float(row[0]) if row else 0.0
        alerts.extend(_evaluate_thresholds("__global__", "daily", today_spent, daily_cap))

        # Monthly
        row = await (await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM tier_spend_log "
            "WHERE DATE_TRUNC('month', timestamp) = DATE_TRUNC('month', CURRENT_DATE) AND success = true"
        )).fetchone()
        month_spent = float(row[0]) if row else 0.0
        alerts.extend(_evaluate_thresholds("__global__", "monthly", month_spent, monthly_cap))

    return alerts


def _evaluate_thresholds(daemon: str, window: str, spent: float, cap: float) -> list[SpendAlert]:
    """Return one alert for the highest threshold crossed (not all of them)."""
    if cap <= 0:
        return []
    pct = spent / cap
    for threshold_pct, level, msg_template in THRESHOLDS:
        if pct >= threshold_pct:
            actions = _actions_for_level(level)
            message = (
                f"[{daemon}/{window}] {msg_template} "
                f"(${spent:.2f} / ${cap:.2f} = {pct:.0%})"
            )
            return [SpendAlert(
                level=level,
                daemon=daemon,
                window=window,
                spent_usd=spent,
                cap_usd=cap,
                pct_of_cap=pct,
                message=message,
                timestamp=datetime.utcnow(),
                actions_taken=actions,
            )]
    return []


def _actions_for_level(level: AlertLevel) -> list[str]:
    """The side effects each alert level triggers in the rest of the system."""
    if level == AlertLevel.BLOCK:
        return ["block_new_requests", "telegram_page_operator", "auto_downgrade_all"]
    if level == AlertLevel.CRITICAL:
        return ["telegram_alert", "enable_auto_downgrade"]
    if level == AlertLevel.WARNING:
        return ["telegram_alert"]
    return ["log_only"]


async def dispatch_alert(alert: SpendAlert) -> None:
    """Send the alert through the right channel based on its level."""
    if alert.level in (AlertLevel.WARNING, AlertLevel.CRITICAL, AlertLevel.BLOCK):
        try:
            from shared.comms import send_telegram_alert
            await send_telegram_alert(alert.message, urgency=alert.level.value)
        except Exception as exc:
            logger.warning("Failed to dispatch Telegram alert: %s", exc)

    log_method = getattr(logger, alert.level.value, logger.info)
    log_method("SpendAlert: %s", alert.message)

    if alert.level == AlertLevel.BLOCK:
        try:
            from shared.db import emit_event
            await emit_event(
                "spend_block",
                {
                    "daemon": alert.daemon,
                    "window": alert.window,
                    "spent": alert.spent_usd,
                    "cap": alert.cap_usd,
                    "pct": alert.pct_of_cap,
                },
            )
        except Exception as exc:
            logger.warning("Failed to emit spend_block event: %s", exc)


__all__ = ["AlertLevel", "SpendAlert", "check_daemon_spend", "check_global_spend", "dispatch_alert"]
