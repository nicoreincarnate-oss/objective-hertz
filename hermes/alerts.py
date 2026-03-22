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
        "revenue_expansion_opportunity": lambda p: (
            f"Expansion idea: {p.get('title', 'untitled')} "
            f"({p.get('capability_type', '?')} {p.get('capability_name', '')}) "
            f"ROI {p.get('expected_roi', 0)}"
        ),
        "revenue_expansion_shadow_started": lambda p: (
            f"Shadow started for {p.get('skill', '?')} at {p.get('shadow_percent', 0)}%"
        ),
        "revenue_expansion_shadow_applied": lambda p: (
            f"Shadow traffic routed to {p.get('skill', '?')} for opportunity #{p.get('opportunity_id', '?')}"
        ),
        "revenue_expansion_adopted": lambda p: (
            f"Expansion adopted: {p.get('skill', '?')} (opportunity #{p.get('opportunity_id', '?')})"
        ),
        "revenue_expansion_rejected": lambda p: (
            f"Expansion rejected: {p.get('skill', '?')} (opportunity #{p.get('opportunity_id', '?')})"
        ),
        "revenue_expansion_needs_builder": lambda p: (
            f"Expansion needs ClawdBot builder path: {p.get('title', 'untitled')} — {p.get('reason', '')[:120]}"
        ),
        "revenue_expansion_blueprint_ready": lambda p: (
            f"Expansion blueprint ready via {p.get('builder_skill', '?')} for opportunity #{p.get('opportunity_id', '?')}"
        ),
        "domain_paused": lambda p: (
            f"DOMAIN PAUSED: {p.get('domain', '?')} — "
            f"bounce {p.get('bounce_rate', 0):.1f}%, spam {p.get('spam_rate', 0):.2f}%"
        ),
        "domain_resumed": lambda p: (
            f"Domain resumed: {p.get('domain', '?')} — bounce recovered to {p.get('bounce_rate', 0):.1f}%"
        ),
        "domain_warning": lambda p: (
            f"Domain warning: {p.get('domain', '?')} — bounce {p.get('bounce_rate', 0):.1f}%, "
            f"volume {p.get('action', 'reduced')}"
        ),
        "skill_blocked": lambda p: (
            f"SKILL BLOCKED: {p.get('skill', '?')} — {p.get('details', 'unknown')[:160]}"
        ),
        "expansion_spend_blocked": lambda p: (
            f"Expansion blocked: {p.get('capability', '?')} costs ${p.get('estimated_cost', 0)}/mo, "
            f"only ${p.get('budget_remaining', 0):.0f} remaining"
        ),
        "warmup_graduated": lambda p: (
            f"Warm-up complete after day {p.get('day', '?')}. Reputation-based volume control active."
        ),
        "agent_recommendation": lambda p: (
            f"ClawdBot → {p.get('to', '?')}: {p.get('message', '')[:200]}"
        ),
        "service_signup_needed": lambda p: (
            f"ClawdBot needs {p.get('service', '?')}: {p.get('purpose', '')[:120]}. "
            f"Sign up at {p.get('url', '?')}"
        ),
        "n8n_workflow_completed": lambda p: (
            f"N8N workflow {p.get('webhook_path', '?')}: {'OK' if p.get('ok') else 'FAILED'}"
        ),
        "sleep_cycle_complete": lambda p: (
            f"Sleep cycle #{p.get('cycle_id', '?')}: "
            f"{p.get('proposals', 0)} proposed → {p.get('survived', 0)} survived → "
            f"{p.get('applied', 0)} applied. Top: {p.get('top_change', 'none')[:80]}"
        ),
        "cell_division_proposed": lambda p: (
            f"CELL DIVISION: {p.get('name', '?')} proposed — {p.get('reason', '')[:120]}"
        ),
        "notebooklm_generated": lambda p: (
            f"NotebookLM {p.get('type', '?')}: "
            + (f"🎧 {p['audio_url'][:60]}" if p.get('audio_url') else "")
            + (f" 📊 {p['infographic_url'][:60]}" if p.get('infographic_url') else "")
            or "generated"
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

    # Try to generate a NotebookLM audio briefing + infographic (async, non-blocking)
    try:
        from tools.notebooklm_client import get_notebooklm_status
        if get_notebooklm_status().get("available"):
            from shared.db import insert_task
            await insert_task("notebooklm", {
                "notebook_type": "briefing",
                "pipeline_data": {
                    "total_leads": total_leads,
                    "yesterday_sent": yesterday_sent,
                    "interested": interested,
                    "revenue": float(revenue),
                    "pending_review": pending_review,
                },
                "learnings": [],  # Titan fills these in daily_reflection
                "metrics": {
                    "total_leads": total_leads,
                    "revenue": float(revenue),
                },
            }, dedupe=True)
            message += "\n\n_Audio briefing generating via NotebookLM..._"
    except Exception:
        pass  # NotebookLM is a nice-to-have, not critical

    await _send_telegram(message)
    logger.info("Morning briefing sent")
