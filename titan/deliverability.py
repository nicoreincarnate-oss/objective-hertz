"""
Autonomous email deliverability monitor and domain protection.

Reads outreach_metrics + v_domain_health, enforces volume ramp-up during
warm-up phase, auto-pauses domains with dangerous bounce/spam rates, and
adjusts daily send volume based on reputation signals.

Called as a scheduled task by Perseus (via Titan daemon).
"""

import logging
from datetime import date

from shared.db import emit_event, execute, fetch_all, fetch_one, fetch_val, get_config, set_config

logger = logging.getLogger("perseus.titan.deliverability")

# ── Thresholds ────────────────────────────────────────────────────
# These are conservative; the system can tighten them via titan_rules later.

BOUNCE_RATE_WARNING = 3.0   # percent — reduce volume
BOUNCE_RATE_CRITICAL = 5.0  # percent — pause domain immediately
SPAM_RATE_CRITICAL = 0.1    # percent — any spam complaints are dangerous
OPEN_RATE_HEALTHY = 20.0    # percent — below this, volume should not grow
WARMUP_ADVANCE_THRESHOLD = 0.8

# Warm-up ramp: day → max emails per domain.
# After day 21, the system uses the learned safe ceiling.
WARMUP_RAMP = {
    1: 10, 2: 15, 3: 20, 4: 25, 5: 30,
    6: 40, 7: 50, 8: 60, 9: 75, 10: 90,
    11: 110, 12: 130, 13: 150, 14: 175,
    15: 200, 16: 250, 17: 300, 18: 350,
    19: 400, 20: 500, 21: 600,
}
WARMUP_GRADUATED_DAY = 21
POST_WARMUP_DEFAULT_CEILING = 1000


async def monitor_deliverability():
    """Main entry point — called by Titan's task handler."""
    await _enforce_warmup_volume()
    await _check_domain_health()
    await _adjust_daily_ceiling()


# ── Warm-up volume enforcement ────────────────────────────────────

async def _enforce_warmup_volume():
    """During warm-up, cap the daily email target to the ramp schedule."""
    warm_up = await get_config("warm_up_phase", True)
    if not warm_up:
        return

    warmup_day = int(await get_config("warmup_day", 1) or 1)

    if warmup_day > WARMUP_GRADUATED_DAY:
        await set_config("warm_up_phase", False)
        await emit_event("warmup_graduated", {
            "day": warmup_day,
            "message": "Domain warm-up complete. Switching to reputation-based volume control.",
        })
        logger.info("Warm-up phase graduated after day %d", warmup_day)
        return

    max_today = WARMUP_RAMP.get(warmup_day, POST_WARMUP_DEFAULT_CEILING)
    current_target = int(await get_config("email_daily_target", 1000) or 1000)

    if current_target > max_today:
        await set_config("email_daily_target", max_today)
        logger.info("Warm-up day %d: capped daily target to %d (was %d)", warmup_day, max_today, current_target)

    # Only advance warm-up after we hit most of today's ramp target.
    sent_today = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0

    minimum_to_advance = max(1, int(max_today * WARMUP_ADVANCE_THRESHOLD))
    if sent_today >= minimum_to_advance:
        await set_config("warmup_day", warmup_day + 1)


# ── Domain health checks ─────────────────────────────────────────

async def _check_domain_health():
    """Read v_domain_health and auto-pause domains that are in trouble."""
    rows = await fetch_all(
        """SELECT domain, date, emails_sent, bounce_rate, open_rate, reply_rate, health_status
           FROM v_domain_health
           WHERE date >= CURRENT_DATE - INTERVAL '2 days'
           ORDER BY date DESC"""
    )

    if not rows:
        return

    # Group by domain, take most recent day
    seen: set[str] = set()
    for row in rows:
        domain = row["domain"]
        if domain in seen:
            continue
        seen.add(domain)

        bounce_rate = float(row.get("bounce_rate", 0) or 0)
        emails_sent = int(row.get("emails_sent", 0) or 0)
        health = row.get("health_status", "OK")

        if emails_sent < 10:
            continue  # Not enough data to judge

        # Check spam complaints from raw metrics
        spam_row = await fetch_one(
            """SELECT spam_complaints, emails_sent FROM outreach_metrics
               WHERE domain = %s AND date = %s""",
            (domain, row["date"]),
        )
        spam_rate = 0.0
        if spam_row and spam_row["emails_sent"] > 0:
            spam_rate = (spam_row.get("spam_complaints", 0) or 0) / spam_row["emails_sent"] * 100

        paused_domains = await get_config("paused_domains", []) or []

        # Critical: pause immediately
        if bounce_rate >= BOUNCE_RATE_CRITICAL or spam_rate >= SPAM_RATE_CRITICAL:
            if domain not in paused_domains:
                paused_domains.append(domain)
                await set_config("paused_domains", paused_domains)
                await emit_event("domain_paused", {
                    "domain": domain,
                    "bounce_rate": bounce_rate,
                    "spam_rate": spam_rate,
                    "reason": "critical_reputation",
                    "action": "auto_paused",
                })
                await emit_event("urgent_alert", {
                    "sender": "titan.deliverability",
                    "message": (
                        f"DOMAIN PAUSED: {domain} — bounce {bounce_rate:.1f}%, "
                        f"spam {spam_rate:.2f}%. Sending stopped to protect reputation."
                    ),
                })
                logger.warning("AUTO-PAUSED domain %s: bounce=%.1f%% spam=%.2f%%", domain, bounce_rate, spam_rate)

        # Warning: reduce volume
        elif bounce_rate >= BOUNCE_RATE_WARNING:
            current_target = int(await get_config("email_daily_target", 1000) or 1000)
            reduced = max(20, int(current_target * 0.5))
            if reduced < current_target:
                await set_config("email_daily_target", reduced)
                await emit_event("domain_warning", {
                    "domain": domain,
                    "bounce_rate": bounce_rate,
                    "action": f"reduced_volume_to_{reduced}",
                })
                logger.info("Reduced daily target to %d due to %s bounce rate %.1f%%", reduced, domain, bounce_rate)

        # Recovery: unpause if metrics improved
        elif domain in paused_domains and bounce_rate < BOUNCE_RATE_WARNING:
            paused_domains.remove(domain)
            await set_config("paused_domains", paused_domains)
            await emit_event("domain_resumed", {
                "domain": domain,
                "bounce_rate": bounce_rate,
                "action": "auto_resumed",
            })
            logger.info("Resumed domain %s after bounce rate recovered to %.1f%%", domain, bounce_rate)


# ── Dynamic volume ceiling ────────────────────────────────────────

async def _adjust_daily_ceiling():
    """After warm-up, dynamically adjust the daily email ceiling based on reputation."""
    warm_up = await get_config("warm_up_phase", True)
    if warm_up:
        return  # Warm-up ramp handles this

    # Get last 7 days aggregate
    stats = await fetch_one(
        """SELECT
               COALESCE(AVG(bounce_rate), 0) AS avg_bounce,
               COALESCE(AVG(open_rate), 0) AS avg_open,
               COALESCE(SUM(emails_sent), 0) AS total_sent
           FROM v_domain_health
           WHERE date >= CURRENT_DATE - INTERVAL '7 days'"""
    )

    if not stats or stats["total_sent"] < 50:
        return

    avg_bounce = float(stats["avg_bounce"])
    avg_open = float(stats["avg_open"])
    current_target = int(await get_config("email_daily_target", 1000) or 1000)

    # Good reputation: increase volume 20%
    if avg_bounce < 1.0 and avg_open >= OPEN_RATE_HEALTHY:
        new_target = min(2000, int(current_target * 1.2))
        if new_target > current_target:
            await set_config("email_daily_target", new_target)
            logger.info("Reputation healthy — increased daily target to %d", new_target)

    # Decent reputation: hold steady
    elif avg_bounce < BOUNCE_RATE_WARNING and avg_open >= 10:
        pass  # No change

    # Poor reputation: reduce
    elif avg_bounce >= BOUNCE_RATE_WARNING or avg_open < 10:
        new_target = max(20, int(current_target * 0.7))
        if new_target < current_target:
            await set_config("email_daily_target", new_target)
            logger.info("Reputation degrading — reduced daily target to %d", new_target)


async def get_send_budget_today() -> int:
    """How many more emails can be sent today, respecting the daily ceiling and paused domains."""
    daily_target = int(await get_config("email_daily_target", 1000) or 1000)
    sent_today = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    return max(0, daily_target - int(sent_today))


async def is_domain_paused(domain: str) -> bool:
    """Check if a specific sending domain is currently paused."""
    paused = await get_config("paused_domains", []) or []
    return domain in paused
