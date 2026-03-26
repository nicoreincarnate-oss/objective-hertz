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
        "Communications gateway — multi-channel alerts, operator interface, "
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

async def _message_send(
    text: str = "",
    channel: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    **_,
) -> dict:
    """Send a message to the operator via the configured OpenJarvis channel."""
    if not text:
        return {"error": "text is required"}
    await db.emit_event("operator_notification", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import send_operator_message

        delivery = await send_operator_message(
            f"*[Message]* {text}",
            target=channel,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        delivery["message"] = text[:200]
        return delivery
    except Exception as exc:
        logger.warning("Channel delivery failed for message_send: %s", exc)
        return {"sent": False, "channel": channel or "unknown", "message": text[:200]}


async def _message_broadcast(
    text: str = "",
    channel: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    **_,
) -> dict:
    """Broadcast a message to all channels."""
    await db.emit_event("broadcast", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import send_operator_message

        delivery = await send_operator_message(
            f"*[Broadcast]* {text}",
            target=channel,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        delivery["broadcast"] = True
        delivery["message"] = text[:200]
        return delivery
    except Exception as exc:
        logger.warning("Channel delivery failed for broadcast: %s", exc)
        return {"broadcast": True, "sent": False, "message": text[:200]}


async def _alert_urgent(
    text: str = "",
    channel: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    **_,
) -> dict:
    await db.emit_event("urgent_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import send_operator_message

        delivery = await send_operator_message(
            f"\U0001f6a8 *[URGENT]* {text}",
            target=channel,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        delivery["alerted"] = delivery.get("sent", False)
        delivery["level"] = "urgent"
        delivery["message"] = text[:200]
        return delivery
    except Exception as exc:
        logger.warning("Channel delivery failed for urgent alert: %s", exc)
        return {"alerted": False, "level": "urgent", "message": text[:200]}


async def _alert_warning(
    text: str = "",
    channel: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    **_,
) -> dict:
    await db.emit_event("warning_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import send_operator_message

        delivery = await send_operator_message(
            f"\u26a0\ufe0f *[WARNING]* {text}",
            target=channel,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        delivery["alerted"] = delivery.get("sent", False)
        delivery["level"] = "warning"
        delivery["message"] = text[:200]
        return delivery
    except Exception as exc:
        logger.warning("Channel delivery failed for warning alert: %s", exc)
        return {"alerted": False, "level": "warning", "message": text[:200]}


async def _alert_info(
    text: str = "",
    channel: str = "",
    conversation_id: str = "",
    metadata: dict | None = None,
    **_,
) -> dict:
    await db.emit_event("info_alert", {"message": text, "sender": "openjarvis"})
    try:
        from hermes.alerts import send_operator_message

        delivery = await send_operator_message(
            f"\u2139\ufe0f *[INFO]* {text}",
            target=channel,
            conversation_id=conversation_id,
            metadata=metadata,
        )
        delivery["alerted"] = delivery.get("sent", False)
        delivery["level"] = "info"
        delivery["message"] = text[:200]
        return delivery
    except Exception as exc:
        logger.warning("Channel delivery failed for info alert: %s", exc)
        return {"alerted": False, "level": "info", "message": text[:200]}


async def _briefing_generate(**_) -> dict:
    """Generate a morning briefing from pipeline state."""
    from shared.pipeline import assess_pipeline_state
    state = await assess_pipeline_state()

    budget = {}
    try:
        from tools.budget_guard import BudgetGuard
        budget = await BudgetGuard().check_budget()
    except Exception as e:
        logger.debug("Budget check failed: %s", e)

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


async def _operator_send_message(text: str = "", target: str = "perseus", priority: str = "normal", _caller: str = "", **_) -> dict:
    """Send an operator-style message to a target daemon.

    FAIL-CLOSED: caller identity is REQUIRED. If _caller is missing or
    not in the allowlist, the request is rejected. This prevents any
    process with the A2A transport secret from silently impersonating
    operator commands by omitting the caller field.
    """
    allowed_callers = {"war_room", "operator", "openjarvis", "hermes"}
    if not _caller or _caller not in allowed_callers:
        logger.warning("operator_send_message rejected: caller '%s' is not operator-privileged (must be one of %s)", _caller or "<missing>", allowed_callers)
        return {"sent": False, "error": f"caller identity required and must be one of {sorted(allowed_callers)}"}

    task_type = f"{target}_operator_message"
    await db.insert_task(task_type, {"message": text, "priority": priority, "source": _caller})
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

    answer = await llm.generate(prompt, model="fast", max_tokens=300)
    return {"answer": answer, "from": "hermes"}


async def _review_finding(finding: dict = None, code_snippet: str = "", **_) -> dict:
    """Review a self-audit finding against actual code.

    Hermes reviews for: alerting reliability, event dispatch correctness,
    Telegram/notification bugs, operator communication safety, and logging gaps.
    """
    if not finding or not code_snippet:
        return {"vote": "defer", "reason": "no finding or code provided", "from": "hermes"}

    from shared.llm_client import llm
    prompt = (
        f"You are Hermes, the operator communication agent. A self-audit found an issue. "
        f"Review the ACTUAL CODE and the proposed fix.\n\n"
        f"FILE: {finding.get('file', '?')}\n"
        f"ISSUE: {finding.get('issue', '?')}\n"
        f"SEVERITY: {finding.get('severity', '?')}\n"
        f"FAILURE MODE: {finding.get('failure_mode', '?')}\n"
        f"PROPOSED FIX: {finding.get('proposed_fix', 'none')}\n"
        f"REASONING: {finding.get('reasoning', '?')}\n\n"
        f"ACTUAL CODE:\n```python\n{code_snippet[:4000]}\n```\n\n"
        f"Review from your perspective:\n"
        f"1. Does this code affect alerting, event dispatch, Telegram, or operator comms?\n"
        f"2. Is the reported issue real? Can you see the bug in the code above?\n"
        f"3. Could the proposed fix cause silent alert failures or missed notifications?\n"
        f"4. Are there logging gaps that would hide problems?\n\n"
        f"Vote: approve (issue is real AND fix is safe), reject (false positive OR fix is dangerous), "
        f"or defer (not in your domain). Include your reasoning."
    )

    answer = await llm.generate(prompt, model="smart", max_tokens=400, temperature=0.1)
    lower = answer.lower()
    if "reject" in lower[:100] or "false positive" in lower[:200]:
        vote = "reject"
    elif "approve" in lower[:100] or "issue is real" in lower[:200]:
        vote = "approve"
    else:
        vote = "defer"

    return {"vote": vote, "reason": answer[:500], "from": "hermes"}


CAPABILITY_HANDLERS = {
    "ask": _ask,
    "review_finding": _review_finding,
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
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug("JSON parse failed in A2A handler: %s", e)

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


def create_hermes_a2a(hermes_daemon=None) -> FastAPI:  # noqa: F821
    health_fn = hermes_daemon.health_check if hermes_daemon else None
    return create_a2a_app(agent_card=HERMES_CARD, handler=handle_a2a, health_check=health_fn)
