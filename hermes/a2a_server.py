"""Hermes A2A Server — exposes Hermes capabilities via OpenJarvis A2A protocol.

Capabilities: messaging, alerts, briefings, operator interface, health.
"""

from __future__ import annotations

import json
import logging

from shared import db
from shared.a2a_wrapper import AgentCard, create_a2a_app

logger = logging.getLogger("perseus.hermes.a2a")


HERMES_CARD = AgentCard(
    name="hermes",
    description=(
        "Communications gateway — Telegram bot, alert dispatcher, operator interface, "
        "morning briefings, and dashboard backend. The voice of the system."
    ),
    url="http://localhost:9002",
    version="1.0.0",
    capabilities=[
        "ask",
        "event_forward",
        "message_send", "message_broadcast",
        "alert_urgent", "alert_warning", "alert_info",
        "briefing_generate", "briefing_custom",
        "operator_last_message", "operator_pending_approvals",
        "operator_send_message",
        "events_recent", "events_by_type",
        "agent_status",
        "health_check",
    ],
)


# ── Capability handlers ───────────────────────────────────────────────

async def _message_send(text: str = "", **_) -> dict:
    """Send a message to the operator via Telegram and store as event."""
    if not text:
        return {"error": "text is required"}
    await db.emit_event("operator_notification", {"message": text, "sender": "openjarvis"})
    # Actually deliver via Telegram
    try:
        from hermes.alerts import _send_telegram
        await _send_telegram(f"*[Message]* {text}")
    except Exception as exc:
        logger.warning("Telegram delivery failed for message_send: %s", exc)
    return {"sent": True, "channel": "telegram", "message": text[:200]}


async def _message_broadcast(text: str = "", **_) -> dict:
    """Broadcast a message to all channels."""
    await db.emit_event("broadcast", {"message": text, "sender": "openjarvis"})
    # Deliver broadcast via Telegram as well
    try:
        from hermes.alerts import _send_telegram
        await _send_telegram(f"*[Broadcast]* {text}")
    except Exception as exc:
        logger.warning("Telegram delivery failed for broadcast: %s", exc)
    return {"broadcast": True, "message": text[:200]}


async def _alert_urgent(text: str = "", **_) -> dict:
    await db.emit_event("urgent_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import _send_telegram
        await _send_telegram(f"\U0001f6a8 *[URGENT]* {text}")
    except Exception as exc:
        logger.warning("Telegram delivery failed for urgent alert: %s", exc)
    return {"alerted": True, "level": "urgent", "message": text[:200]}


async def _alert_warning(text: str = "", **_) -> dict:
    await db.emit_event("warning_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import _send_telegram
        await _send_telegram(f"\u26a0\ufe0f *[WARNING]* {text}")
    except Exception as exc:
        logger.warning("Telegram delivery failed for warning alert: %s", exc)
    return {"alerted": True, "level": "warning", "message": text[:200]}


async def _alert_info(text: str = "", **_) -> dict:
    await db.emit_event("info_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import _send_telegram
        await _send_telegram(f"\u2139\ufe0f *[INFO]* {text}")
    except Exception as exc:
        logger.warning("Telegram delivery failed for info alert: %s", exc)
    return {"alerted": True, "level": "info", "message": text[:200]}


async def _briefing_generate(**_) -> dict:
    """Generate a morning briefing from pipeline state."""
    from shared.pipeline import assess_pipeline_state
    state = await assess_pipeline_state()

    budget = {}
    try:
        from tools.budget_guard import BudgetGuard
        budget = await BudgetGuard().check_budget()
    except Exception:
        pass

    decisions = await db.fetch_all(
        "SELECT agent, decision_type, reasoning FROM agent_decisions ORDER BY created_at DESC LIMIT 5"
    )

    briefing = {
        "pipeline": state,
        "budget": budget,
        "recent_decisions": [dict(d) for d in decisions],
    }
    return briefing


async def _briefing_custom(topic: str = "", **_) -> dict:
    """Generate a briefing on a specific topic."""
    if not topic:
        return {"error": "topic is required"}
    # Search learnings related to the topic
    from shared.comms import get_learnings, search_vector_memory
    learnings = await get_learnings(limit=10)
    memories = await search_vector_memory(topic, limit=5)
    return {
        "topic": topic,
        "learnings": [dict(l) for l in learnings],
        "memories": memories,
    }


async def _operator_last_message(**_) -> dict:
    """Get the last operator message."""
    row = await db.fetch_one(
        """SELECT payload, created_at FROM events
           WHERE event_type IN ('operator_message', 'perseus_operator_message', 'clawdbot_operator_message')
           ORDER BY created_at DESC LIMIT 1"""
    )
    if row:
        return {"message": row.get("payload", {}), "at": str(row.get("created_at", ""))}
    return {"message": None}


async def _operator_pending_approvals(**_) -> dict:
    """List items awaiting operator approval."""
    try:
        rows = await db.fetch_all(
            "SELECT * FROM review_queue WHERE status = 'pending_review' ORDER BY created_at DESC LIMIT 20"
        )
        return {"pending": [dict(r) for r in rows], "count": len(rows)}
    except Exception:
        return {"pending": [], "count": 0}


async def _operator_send_message(text: str = "", target: str = "perseus", priority: str = "normal", **_) -> dict:
    """Send an operator-style message to a target daemon."""
    task_type = f"{target}_operator_message"
    await db.insert_task(task_type, {"message": text, "priority": priority, "source": "openjarvis"})
    return {"sent": True, "target": target}


async def _events_recent(limit: int = 20, **_) -> list:
    rows = await db.fetch_all(
        "SELECT event_type, payload, created_at FROM events ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    return [dict(r) for r in rows]


async def _events_by_type(event_type: str = "", limit: int = 20, **_) -> list:
    if not event_type:
        return []
    rows = await db.fetch_all(
        "SELECT payload, created_at FROM events WHERE event_type = %s ORDER BY created_at DESC LIMIT %s",
        (event_type, limit),
    )
    return [dict(r) for r in rows]


async def _agent_status(**_) -> list:
    rows = await db.fetch_all(
        "SELECT name, status, last_heartbeat FROM agent_registry ORDER BY name"
    )
    return [dict(r) for r in rows]


async def _event_forward(event_type: str = "", **event_payload) -> dict:
    """Receive a forwarded event from another agent and dispatch as alert.

    This is the primary A2A push path for instant alert delivery.
    """
    if not event_type:
        return {"error": "event_type is required"}
    from hermes.alerts import dispatch_alert_for_event
    sent = await dispatch_alert_for_event({"event_type": event_type, **event_payload})
    return {"dispatched": sent, "event_type": event_type}


async def _health_check(**_) -> dict:
    return {"status": "running", "agent": "hermes"}


async def _ask(question: str = "", from_agent: str = "", context: dict = None, **_) -> dict:
    """Handle a question from another agent about operator context."""
    from shared.db import fetch_all

    # Get recent operator messages
    recent_msgs = await fetch_all(
        """SELECT payload, created_at FROM events
           WHERE event_type IN ('operator_message', 'telegram_message')
           ORDER BY created_at DESC LIMIT 10"""
    )
    operator_ctx = "\n".join(
        f"- {str(m.get('payload', ''))[:150]}" for m in (recent_msgs or [])
    )

    from shared.llm_client import llm
    prompt = (
        f"You are Hermes, the operator communication agent. {from_agent} is asking:\n\n"
        f"{question}\n\n"
        f"Recent operator messages:\n{operator_ctx}\n\n"
        f"Answer based on what the operator has communicated. If no relevant context, say so."
    )

    answer = await llm.generate(prompt, tier="fast", max_tokens=300)
    return {"answer": answer, "from": "hermes"}


CAPABILITY_HANDLERS = {
    "ask": _ask,
    "event_forward": _event_forward,
    "message_send": _message_send,
    "message_broadcast": _message_broadcast,
    "alert_urgent": _alert_urgent,
    "alert_warning": _alert_warning,
    "alert_info": _alert_info,
    "briefing_generate": _briefing_generate,
    "briefing_custom": _briefing_custom,
    "operator_last_message": _operator_last_message,
    "operator_pending_approvals": _operator_pending_approvals,
    "operator_send_message": _operator_send_message,
    "events_recent": _events_recent,
    "events_by_type": _events_by_type,
    "agent_status": _agent_status,
    "health_check": _health_check,
}


async def handle_a2a(input_text: str) -> str:
    """Route A2A requests to Hermes capabilities."""
    text = input_text.strip()

    # JSON structured input
    try:
        req = json.loads(text)
        if isinstance(req, dict) and "capability" in req:
            cap = req["capability"]
            params = req.get("params", {})
            handler = CAPABILITY_HANDLERS.get(cap)
            if handler:
                result = await handler(**params)
                return json.dumps(result, indent=2, default=str)
            return json.dumps({"error": f"Unknown capability: {cap}"})
    except (json.JSONDecodeError, TypeError):
        pass

    # Natural language routing
    lower = text.lower()
    if "event_forward" in lower:
        result = await _event_forward(**{})
    elif "briefing" in lower or "morning" in lower or "status" in lower:
        result = await _briefing_generate()
    elif "alert" in lower and "urgent" in lower:
        result = await _alert_urgent(text=text)
    elif "alert" in lower or "warn" in lower:
        result = await _alert_warning(text=text)
    elif "send" in lower or "message" in lower or "tell" in lower:
        result = await _message_send(text=text)
    elif "approval" in lower or "pending" in lower or "review" in lower:
        result = await _operator_pending_approvals()
    elif "agent" in lower and "status" in lower:
        result = await _agent_status()
    elif "event" in lower:
        result = await _events_recent()
    elif "health" in lower:
        result = await _health_check()
    else:
        result = {
            "error": "Hermes couldn't route this request.",
            "available_capabilities": list(CAPABILITY_HANDLERS.keys()),
        }

    return json.dumps(result, indent=2, default=str)


def create_hermes_a2a(hermes_daemon=None) -> "FastAPI":
    health_fn = hermes_daemon.health_check if hermes_daemon else None
    return create_a2a_app(agent_card=HERMES_CARD, handler=handle_a2a, health_check=health_fn)
