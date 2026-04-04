"""Hermes alert dispatcher — sends operator notifications via channel backends."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from shared.config import config
from shared.db import execute, fetch_all

logger = logging.getLogger("perseus.hermes.alerts")

_CHANNEL_BACKEND = None
_CHANNEL_BACKEND_KEY = None


async def dispatch_alerts():
    """Check for unacknowledged events and send operator alerts.

    This is the FALLBACK poll loop. Primary delivery is via A2A push
    through dispatch_alert_for_event().
    """
    events = await fetch_all(
        """SELECT id, event_type, payload, created_at
           FROM events WHERE acknowledged = FALSE
           ORDER BY created_at ASC LIMIT 20"""
    )

    for event in events:
        try:
            # Morning briefing is special — triggers the full briefing function
            if event.get("event_type") == "morning_briefing":
                sent = await send_morning_briefing()
            else:
                message = _format_event(event)
                sent = False
                if message:
                    delivery = await send_operator_message(message)
                    sent = bool(delivery.get("sent"))
            if sent:
                await execute(
                    "UPDATE events SET acknowledged = TRUE WHERE id = %s",
                    (event["id"],),
                )
        except Exception as e:
            logger.error(f"Alert dispatch failed for event {event['id']}: {e}")


async def dispatch_alert_for_event(event: dict) -> bool:
    """Dispatch a single event as an operator alert (called via A2A push).

    This is the PRIMARY delivery path — events arrive instantly via A2A
    instead of waiting for the poll cycle.

    Args:
        event: dict with at least "event_type" key and optional payload fields.

    Returns:
        True if alert was sent successfully.
    """
    event_type = event.get("event_type", "")
    if not event_type:
        return False

    try:
        if event_type == "morning_briefing":
            return await send_morning_briefing()

        formatted = {"event_type": event_type, "payload": event}
        message = _format_event(formatted)
        if message:
            delivery = await send_operator_message(message)
            return bool(delivery.get("sent"))
    except Exception as e:
        logger.error(f"A2A alert dispatch failed for {event_type}: {e}")

    return False


def _format_event(event: dict) -> str:
    """Format an event into an operator-facing message."""
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
        "approval_requested": lambda p: (
            f"APPROVAL REQUIRED: {p.get('type', 'unknown')} "
            f"by {p.get('requested_by', 'unknown')} -- "
            f"{str(p.get('details', {}))[:200]}. Review at /api/approvals"
        ),
        "approval_resolved": lambda p: (
            f"Approval {p.get('approval_id', '?')[:8]} "
            f"{p.get('decision', 'unknown')} by {p.get('resolved_by', 'unknown')}"
        ),
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


def _get_channel_backend():
    """Resolve and cache the configured OpenJarvis channel backend."""
    global _CHANNEL_BACKEND, _CHANNEL_BACKEND_KEY

    try:
        from openjarvis.core.config import load_config
        from openjarvis.system import SystemBuilder
        from shared.oj_bridge import get_bus

        oj_config = load_config()
        channel_key = (oj_config.channel.default_channel or "").strip()
        if not oj_config.channel.enabled or not channel_key:
            return None, oj_config, ""

        if _CHANNEL_BACKEND is None or _CHANNEL_BACKEND_KEY != channel_key:
            builder = SystemBuilder()
            _CHANNEL_BACKEND = builder._resolve_channel(oj_config, get_bus())
            _CHANNEL_BACKEND_KEY = channel_key

        if _CHANNEL_BACKEND is not None:
            try:
                _CHANNEL_BACKEND.connect()
            except Exception:
                logger.debug("Channel connect failed for %s", channel_key, exc_info=True)

        return _CHANNEL_BACKEND, oj_config, channel_key
    except Exception:
        logger.debug("Failed to resolve OpenJarvis channel backend", exc_info=True)
        return None, None, ""


def _resolve_channel_target(channel_key: str, oj_config, explicit_target: str = "") -> str:
    """Resolve the target chat/room/address for the configured channel."""
    if explicit_target:
        return explicit_target

    generic = os.environ.get("OPENJARVIS_CHANNEL_TARGET", "").strip()
    if generic:
        return generic

    env_map = {
        "discord": ("DISCORD_CHANNEL_ID", "DISCORD_DEFAULT_CHANNEL"),
        "slack": ("SLACK_CHANNEL_ID", "SLACK_DEFAULT_CHANNEL"),
        "email": ("EMAIL_TO", "EMAIL_RECIPIENT"),
        "whatsapp": ("WHATSAPP_TO",),
        "signal": ("SIGNAL_TO", "SIGNAL_RECIPIENT"),
        "google_chat": ("GOOGLE_CHAT_SPACE",),
        "irc": ("IRC_CHANNEL",),
        "webchat": ("WEBCHAT_SESSION_ID",),
        "teams": ("TEAMS_CHANNEL_ID", "TEAMS_CONVERSATION_ID"),
        "matrix": ("MATRIX_ROOM_ID", "MATRIX_DEFAULT_ROOM"),
        "mattermost": ("MATTERMOST_CHANNEL_ID", "MATTERMOST_DEFAULT_CHANNEL"),
        "feishu": ("FEISHU_CHAT_ID",),
        "bluebubbles": ("BLUEBUBBLES_CHAT_GUID",),
        "whatsapp_baileys": ("WHATSAPP_TO",),
        "webhook": ("WEBHOOK_TARGET",),
    }
    for env_name in env_map.get(channel_key, ()):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value

    if channel_key == "telegram":
        shared_chat_id = str(getattr(config.telegram, "chat_id", "") or "").strip()
        if shared_chat_id:
            return shared_chat_id
        allowed = getattr(getattr(oj_config, "channel", None), "telegram", None)
        allowed_ids = str(getattr(allowed, "allowed_chat_ids", "") or "").strip()
        if allowed_ids:
            return allowed_ids.split(",")[0].strip()

    if channel_key == "email":
        email_cfg = getattr(getattr(oj_config, "channel", None), "email", None)
        username = str(getattr(email_cfg, "username", "") or "").strip()
        if username:
            return username

    if channel_key in {"google_chat", "webhook", "webchat"}:
        return channel_key

    return ""


async def send_operator_message(
    message: str,
    *,
    target: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    allow_telegram_fallback: bool = True,
) -> dict:
    """Send a Hermes operator message via the configured OpenJarvis channel."""
    backend, oj_config, channel_key = _get_channel_backend()
    if backend is not None and channel_key:
        resolved_target = _resolve_channel_target(channel_key, oj_config, target)
        if resolved_target:
            try:
                ok = await asyncio.to_thread(
                    backend.send,
                    resolved_target,
                    message,
                    conversation_id=conversation_id,
                    metadata=metadata or {},
                )
            except Exception:
                logger.warning(
                    "Configured channel delivery failed for %s",
                    channel_key,
                    exc_info=True,
                )
                ok = False
            if ok:
                return {
                    "sent": True,
                    "channel": channel_key,
                    "target": resolved_target,
                    "fallback": False,
                }
            logger.warning(
                "Configured channel %s could not deliver Hermes message to %s",
                channel_key,
                resolved_target or "<default>",
            )
        else:
            logger.warning(
                "No default target configured for Hermes channel backend %s",
                channel_key,
            )

    if allow_telegram_fallback:
        ok = await _send_telegram(message)
        if ok:
            return {
                "sent": True,
                "channel": "telegram",
                "target": str(getattr(config.telegram, "chat_id", "") or "").strip(),
                "fallback": True,
            }

    return {
        "sent": False,
        "channel": channel_key or "none",
        "target": target,
        "fallback": bool(channel_key),
    }


async def _send_telegram(message: str) -> bool:
    """Send a message to Nico via Telegram."""
    if not config.telegram.bot_token or not config.telegram.chat_id:
        logger.warning("Telegram not configured, skipping alert")
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{config.telegram.bot_token}/sendMessage",
                json={
                    "chat_id": config.telegram.chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


async def send_morning_briefing() -> bool:
    """Send the daily morning briefing to Nico."""
    from pathlib import Path

    from shared.db import fetch_val

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

    # Ruflo engineering stats
    ruflo_section = ""
    try:
        from shared.config import config
        if config.ruflo.enabled:
            ruflo_fixes = await fetch_val(
                "SELECT COUNT(*) FROM ruflo_tasks WHERE validation_status = 'passed' "
                "AND created_at > NOW() - INTERVAL '24 hours'"
            ) or 0
            ruflo_pending = await fetch_val(
                "SELECT COUNT(*) FROM ruflo_tasks WHERE status IN ('pending', 'dispatched', 'running')"
            ) or 0
            ruflo_spend = await fetch_val(
                "SELECT COALESCE(SUM(claude_cost), 0) FROM ruflo_tasks "
                "WHERE created_at > DATE_TRUNC('month', NOW())"
            ) or 0
            ruflo_section = (
                f"\n*Ruflo (24h):*\n"
                f"  Fixes validated: {ruflo_fixes}\n"
                f"  In progress: {ruflo_pending}\n"
                f"  Claude spend this month: ${float(ruflo_spend):.2f}/${config.ruflo.claude_monthly_cap:.0f}\n"
            )
    except Exception:
        pass

    deerflow_section = ""
    try:
        brief_dir = Path(__file__).resolve().parents[2] / "output" / "deerflow" / "daily"
        latest_brief = sorted(brief_dir.glob("*-daily-evolution-brief.md"))[-1] if brief_dir.exists() else None
        if latest_brief is not None:
            deerflow_section = (
                f"\n*DeerFlow (24h):*\n"
                f"  Latest brief: {latest_brief.name}\n"
                f"  Path: {latest_brief}\n"
            )
    except Exception:
        pass

    message = (
        f"*Good morning, Nico!*\n\n"
        f"*Yesterday:*\n"
        f"  Emails sent: {yesterday_sent}\n"
        f"  Total leads: {total_leads}\n"
        f"  Interested: {interested}\n\n"
        f"*Revenue:* ${revenue:.2f}\n"
        f"*Pending review:* {pending_review}\n"
        f"{ruflo_section}\n"
        f"{deerflow_section}\n"
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
    except Exception as e:
        logger.debug("Alert processing failed: %s", e)

    delivery = await send_operator_message(message)
    if delivery.get("sent"):
        logger.info(
            "Morning briefing sent via %s",
            delivery.get("channel", "unknown"),
        )
        return True
    logger.warning("Morning briefing delivery failed")
    return False
