"""
Hermes alert dispatcher — sends Telegram notifications for important events.
"""

import logging
import httpx
from shared.config import config
from shared.db import fetch_all, execute

logger = logging.getLogger("perseus.hermes.alerts")


async def dispatch_alerts():
    """Check for unacknowledged events and send Telegram alerts."""
    events = await fetch_all(
        """SELECT id, event_type, payload, created_at
           FROM events WHERE acknowledged = FALSE
           ORDER BY created_at ASC LIMIT 20"""
    )

    for event in events:
        try:
            # Morning briefing is special — triggers the full briefing function
            if event.get("event_type") == "morning_briefing":
                await send_morning_briefing()
            else:
                message = _format_event(event)
                if message:
                    await _send_telegram(message)
            await execute(
                "UPDATE events SET acknowledged = TRUE WHERE id = %s",
                (event["id"],),
            )
        except Exception as e:
            logger.error(f"Alert dispatch failed for event {event['id']}: {e}")


def _format_event(event: dict) -> str:
    """Format an event into a Telegram message."""
    etype = event.get("event_type", "")
    payload = event.get("payload", {})

    formatters = {
        "leads_discovered": lambda p: f"Found {p.get('count', 0)} new leads",
        "lead_discovery_empty": lambda p: (
            f"Lead discovery returned 0 leads via {p.get('source', 'unknown')}"
        ),
        "memory_write_failed": lambda p: (
            f"Memory write degraded: {p.get('error', 'unknown')[:160]}"
        ),
        "emails_sent": lambda p: f"Sent {p.get('count', 0)} emails ({p.get('daily_total', 0)} today)",
        "lead_interested": lambda p: f"HOT LEAD interested! Client #{p.get('client_id', '?')}",
        "review_needed": lambda p: f"Review needed: {p.get('count', 1)} {p.get('type', 'items')}",
        "deal_closed": lambda p: f"SALE #{p.get('sale_number', '?')} closed! Client #{p.get('client_id', '?')}",
        "payment_received": lambda p: f"PAYMENT received: ${p.get('amount', 0)}",
        "site_deployed": lambda p: f"Site deployed: {p.get('url', '')} for {p.get('business_name', '')}",
        "autonomy_unlocked": lambda p: f"AUTONOMY UNLOCKED! {p.get('message', '')}",
        "budget_exceeded": lambda p: "BUDGET WARNING — limit reached",
        "pipeline_error": lambda p: (
            f"Pipeline error in {p.get('stage', '?')}: "
            f"{p.get('error_type', 'Error')} — {p.get('error', 'unknown')[:160]}"
        ),
        "pipeline_stage_error": lambda p: (
            f"Pipeline stage failed: {p.get('stage', '?')} — {p.get('error', 'unknown')[:160]}"
        ),
        "titan_error": lambda p: f"Titan error: {p.get('error', 'unknown')[:200]}",
        "clawdbot_error": lambda p: f"ClawdBot error: {p.get('error', 'unknown')[:200]}",
        "urgent_alert": lambda p: f"ALERT from {p.get('sender', '?')}: {p.get('message', '')}",
        "site_down": lambda p: f"SITE DOWN: {p.get('url', '')} (client #{p.get('client_id', '?')})",
        "lead_enriched": lambda p: f"Lead enriched: {p.get('business_name', '')}",
        "health_report": lambda p: f"Health: {', '.join(f'{k}={v}' for k, v in (p.get('agents') or {}).items()) or 'OK'}",
        "budget_report": lambda p: f"Budget: ${p.get('total_spent', 0):.0f}/${p.get('cap', 800)} ({p.get('percent_used', 0):.0f}%)" if p.get('total_spent') else None,
    }

    formatter = formatters.get(etype)
    if formatter:
        return f"*[Perseus]* {formatter(payload)}"
    return f"*[Perseus]* {etype}: {str(payload)[:200]}"


async def _send_telegram(message: str):
    """Send a message to Nico via Telegram."""
    if not config.telegram.bot_token or not config.telegram.chat_id:
        logger.warning("Telegram not configured, skipping alert")
        return

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{config.telegram.bot_token}/sendMessage",
            json={
                "chat_id": config.telegram.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
        )


async def send_morning_briefing():
    """Send the daily morning briefing to Nico."""
    from shared.db import fetch_val, fetch_all

    total_leads = await fetch_val("SELECT COUNT(*) FROM clients") or 0
    yesterday_sent = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE - 1"
    ) or 0
    interested = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'interested'"
    ) or 0
    revenue = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
    ) or 0
    pending_review = await fetch_val(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
    ) or 0

    message = (
        f"*Good morning, Nico!*\n\n"
        f"*Yesterday:*\n"
        f"  Emails sent: {yesterday_sent}\n"
        f"  Total leads: {total_leads}\n"
        f"  Interested: {interested}\n\n"
        f"*Revenue:* ${revenue:.2f}\n"
        f"*Pending review:* {pending_review}\n\n"
        f"_Perseus is running. Titan is working._"
    )

    await _send_telegram(message)
    logger.info("Morning briefing sent")
