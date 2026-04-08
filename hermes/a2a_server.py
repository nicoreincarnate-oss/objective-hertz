"""Hermes A2A Server — exposes Hermes capabilities via OpenJarvis A2A protocol.

Capabilities: messaging, alerts, briefings, operator interface, health.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from shared import db
from shared.a2a_wrapper import AgentCard, create_a2a_app

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("perseus.hermes.a2a")


# ── Task 19-03: Response cache for A2A LLM calls ────────────────────

def _cost_dashboard_enabled() -> bool:
    """Check whether the ANATOMY_COST_DASHBOARD feature flag is active."""
    return os.environ.get("ANATOMY_COST_DASHBOARD", "").lower() in ("true", "1")


class _ResponseCache:
    """Simple TTL response cache to eliminate redundant LLM calls.

    Keys are hashed from (question + agent_name). Entries expire after
    ``ttl_seconds`` (default 300 = 5 minutes).

    Gated behind the ANATOMY_COST_DASHBOARD feature flag.
    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _make_key(question: str, agent_name: str) -> str:
        raw = f"{question}:{agent_name}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, question: str, agent_name: str) -> Any | None:
        """Return cached response or None if miss / expired."""
        if not _cost_dashboard_enabled():
            return None
        key = self._make_key(question, agent_name)
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self.ttl:
            del self._store[key]
            return None
        return value

    def put(self, question: str, agent_name: str, value: Any) -> None:
        """Store a response in the cache."""
        if not _cost_dashboard_enabled():
            return
        key = self._make_key(question, agent_name)
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()


_ask_cache = _ResponseCache(ttl_seconds=300)


def _coordination_v2_enabled() -> bool:
    return os.environ.get("ANATOMY_COORDINATION_V2", "").lower() in ("true", "1")


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
        "jarvis_execute",
        "jarvis_plan",
        "jarvis_observe",
        "jarvis_click",
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
    except (ImportError, OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (ImportError, OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (ImportError, OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (ImportError, OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (ImportError, OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
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
    except (ImportError, OSError, RuntimeError, ValueError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("Budget check failed: %s", e)

    decisions = await db.fetch_all(
        "SELECT agent, decision_type, reasoning FROM agent_decisions ORDER BY created_at DESC LIMIT 5"
    )

    # Cost dashboard (Phase 19)
    cost_summary = {}
    try:
        from shared.cost_events import get_agent_cost_summary
        cost_summary = await get_agent_cost_summary(lookback_days=7)
    except (ImportError, OSError, RuntimeError, ValueError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("Cost summary for briefing failed: %s", e)

    briefing = {
        "pipeline": state,
        "budget": budget,
        "recent_decisions": [dict(d) for d in decisions],
        "cost_dashboard": cost_summary,
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
    except (OSError, RuntimeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("Review queue fetch failed: %s", exc)
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


async def _ask(question: str = "", from_agent: str = "", context: dict | None = None, **_) -> dict:
    """Handle a question from another agent about operator context.

    Task 19-03: responses are cached for 5 minutes (keyed on question +
    from_agent) when ANATOMY_COST_DASHBOARD is enabled.
    """
    # Check cache first
    cached = _ask_cache.get(question, from_agent)
    if cached is not None:
        logger.debug("_ask cache hit for question from %s", from_agent)
        return cached

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

    answer = await llm.generate(prompt, model="fast", max_tokens=300, operation="hermes._ask", daemon_name="hermes")
    result = {"answer": answer, "from": "hermes"}

    # Cache the response
    _ask_cache.put(question, from_agent, result)
    return result


async def _review_finding(finding: dict | None = None, code_snippet: str = "", **_) -> dict:
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

    answer = await llm.generate(prompt, model="smart", max_tokens=400, temperature=0.1, operation="hermes._review_finding", daemon_name="hermes")
    return _parse_review_vote(answer, "hermes")


async def review_findings_batch(findings: list[dict]) -> list[dict]:
    """Batch-review up to 5 findings in a single LLM call.

    Task 19-03: when ANATOMY_COST_DASHBOARD is enabled, combines
    multiple findings into one prompt and parses individual verdicts.
    Falls back to sequential single-finding review when the flag is off.
    """
    if not findings:
        return []

    if not _cost_dashboard_enabled() or len(findings) == 1:
        return [await _review_finding(**f) for f in findings]

    # Batch up to 5 findings per LLM call
    batch = findings[:5]
    sections = []
    for i, f in enumerate(batch, 1):
        finding = f.get("finding", {})
        snippet = f.get("code_snippet", "")
        sections.append(
            f"--- FINDING {i} ---\n"
            f"FILE: {finding.get('file', '?')}\n"
            f"ISSUE: {finding.get('issue', '?')}\n"
            f"SEVERITY: {finding.get('severity', '?')}\n"
            f"FAILURE MODE: {finding.get('failure_mode', '?')}\n"
            f"PROPOSED FIX: {finding.get('proposed_fix', 'none')}\n"
            f"REASONING: {finding.get('reasoning', '?')}\n"
            f"CODE:\n```python\n{snippet[:2000]}\n```"
        )

    prompt = (
        "You are Hermes, the operator communication agent. "
        "Review the following self-audit findings. For EACH finding, "
        "vote: approve, reject, or defer. Format your response as:\n"
        "FINDING 1: <vote> — <reason>\n"
        "FINDING 2: <vote> — <reason>\n"
        "...\n\n"
        + "\n\n".join(sections)
    )

    from shared.llm_client import llm
    answer = await llm.generate(prompt, model="smart", max_tokens=600, temperature=0.1, operation="hermes.review_findings_batch", daemon_name="hermes")

    results = _parse_batch_review(answer, len(batch), "hermes")

    # Process remaining findings beyond the batch
    if len(findings) > 5:
        results.extend(await review_findings_batch(findings[5:]))

    return results


def _parse_review_vote(answer: str, agent: str) -> dict:
    """Extract vote from a single-finding review answer."""
    lower = answer.lower()
    if "reject" in lower[:100] or "false positive" in lower[:200]:
        vote = "reject"
    elif "approve" in lower[:100] or "issue is real" in lower[:200]:
        vote = "approve"
    else:
        vote = "defer"
    return {"vote": vote, "reason": answer[:500], "from": agent}


def _parse_batch_review(answer: str, count: int, agent: str) -> list[dict]:
    """Parse individual verdicts from a batched review response."""
    results: list[dict] = []
    lines = answer.strip().splitlines()

    for i in range(1, count + 1):
        found = False
        for line in lines:
            if f"FINDING {i}" in line.upper() or f"FINDING {i}:" in line.upper():
                results.append(_parse_review_vote(line, agent))
                found = True
                break
        if not found:
            results.append({"vote": "defer", "reason": f"Could not parse finding {i}", "from": agent})

    return results

async def _handle_jarvis_execute(input_text: str = "", **_) -> dict:
    """Execute a natural language computer-use command with multi-step planning."""
    if not input_text:
        return {"error": "input_text is required"}
    try:
        from hermes.jarvis.step_planner import plan_command, execute_plan
        from hermes.jarvis.vision_loop import run_vision_loop

        # Plan the command
        plan = await plan_command(input_text)

        if plan.is_simple:
            # Single action — run one vision loop
            result = await run_vision_loop(
                goal=input_text,
                max_iterations=10,
            )
            return {
                "type": "simple",
                "goal": input_text,
                "achieved": result.achieved,
                "steps_taken": len(result.steps),
                "final_description": result.final_description,
                "reason": result.reason,
                "duration_ms": result.total_duration_ms,
            }
        else:
            # Multi-step — run the full plan
            result = await execute_plan(plan)
            return {
                "type": "multi_step",
                "goal": input_text,
                "success": result["success"],
                "completed_steps": result["completed_steps"],
                "total_steps": result["total_steps"],
                "steps": result["steps"],
            }
    except (ImportError, OSError, RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.warning("Jarvis execute failed: %s", e)
        return {"error": str(e), "success": False}


async def _handle_jarvis_plan(input_text: str = "", **_) -> dict:
    """Plan a command without executing — operator can review before running."""
    if not input_text:
        return {"error": "input_text is required"}
    try:
        from hermes.jarvis.step_planner import plan_command

        plan = await plan_command(input_text)
        return {
            "goal": input_text,
            "is_simple": plan.is_simple,
            "steps": [
                {"index": i, "goal": s.goal, "precondition": s.precondition, "expected_outcome": s.expected_outcome}
                for i, s in enumerate(plan.steps)
            ],
            "total_steps": len(plan.steps),
            "success": True,
        }
    except (ImportError, OSError, RuntimeError, ConnectionError, TimeoutError, ValueError) as e:
        logger.warning("Jarvis plan failed: %s", e)
        return {"error": str(e), "success": False}


async def _handle_jarvis_observe(input_text: str = "", **_) -> dict:
    """Take screenshot and describe what's on screen."""
    try:
        from hermes.jarvis.image_pipeline import capture_screenshot, resize_for_vision
        from hermes.jarvis.vision_analyzer import describe_screen

        screenshot = await capture_screenshot(method="native")
        resized = resize_for_vision(screenshot)
        description = await describe_screen(resized)

        return {"description": description, "success": True}
    except (ImportError, OSError, RuntimeError) as exc:
        logger.warning("jarvis_observe failed: %s", exc)
        return {"error": str(exc), "success": False}


async def _handle_jarvis_click(input_text: str = "", **_) -> dict:
    """Click at a described element using Vision-guided targeting."""
    if not input_text:
        return {"error": "input_text is required (describe the element to click)"}
    try:
        from hermes.jarvis.image_pipeline import capture_screenshot, resize_for_vision
        from hermes.jarvis.vision_analyzer import analyze_screenshot
        from hermes.jarvis.action_executor import execute_action

        screenshot = await capture_screenshot(method="native")
        resized = resize_for_vision(screenshot)
        result = await analyze_screenshot(resized, goal=f"click on: {input_text}")

        if result.action_type == "click" and result.action_target:
            action_result = await execute_action("click", result.action_target)
            return {
                "screen_description": result.description,
                "action_taken": action_result.description,
                "success": action_result.success,
                "confidence": result.confidence,
            }

        return {
            "screen_description": result.description,
            "action_taken": "no clickable target identified",
            "success": False,
            "confidence": result.confidence,
        }
    except (ImportError, OSError, RuntimeError) as exc:
        logger.warning("jarvis_click failed: %s", exc)
        return {"error": str(exc), "success": False}


CAPABILITY_HANDLERS: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {
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
    "jarvis_execute": _handle_jarvis_execute,
    "jarvis_plan": _handle_jarvis_plan,
    "jarvis_observe": _handle_jarvis_observe,
    "jarvis_click": _handle_jarvis_click,
}


def list_capabilities() -> list[str]:
    """Return all registered Hermes capability names."""
    return sorted(CAPABILITY_HANDLERS.keys())


async def _nl_route_hermes(text: str) -> dict:
    """Legacy NL fallback routing for backward compatibility.

    Only used when ANATOMY_COORDINATION_V2 is disabled.
    """
    lower = text.lower()
    if "event_forward" in lower:
        return await _event_forward(**{})
    elif "briefing" in lower or "morning" in lower or "status" in lower:
        return await _briefing_generate()
    elif "alert" in lower and "urgent" in lower:
        return await _alert_urgent(text=text)
    elif "alert" in lower or "warn" in lower:
        return await _alert_warning(text=text)
    elif "send" in lower or "message" in lower or "tell" in lower:
        return await _message_send(text=text)
    elif "approval" in lower or "pending" in lower or "review" in lower:
        return await _operator_pending_approvals()
    elif "agent" in lower and "status" in lower:
        return await _agent_status()
    elif "event" in lower:
        return await _events_recent()
    elif "health" in lower:
        return await _health_check()
    else:
        return {
            "error": "Hermes couldn't route this request.",
            "available_capabilities": list_capabilities(),
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
                # Schema validation (warning-only)
                from shared.capability_schema import (
                    HERMES_CAPABILITY_SCHEMAS,
                    warn_on_validation_errors,
                )

                warn_on_validation_errors(
                    cap, params, HERMES_CAPABILITY_SCHEMAS, agent_name="hermes",
                )
                result = await handler(**params)
                return json.dumps(result, indent=2, default=str)
            # Unknown capability — return error with capability list
            logger.warning(
                "Hermes: unknown capability '%s' requested. Available: %s",
                cap,
                list_capabilities(),
            )
            return json.dumps({
                "error": f"Unknown capability: {cap}",
                "available_capabilities": list_capabilities(),
            })
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug("JSON parse failed in A2A handler: %s", e)

    # Non-JSON input: structured-only dispatch vs NL fallback
    if _coordination_v2_enabled():
        # Structured-only mode: reject NL input, log for debugging
        logger.warning(
            "Hermes: non-JSON A2A request rejected (structured-only mode). "
            "Input: %.200s",
            text,
        )
        result = {
            "error": "Structured dispatch only. Send JSON with 'capability' and 'params' keys.",
            "available_capabilities": list_capabilities(),
        }
    else:
        # Legacy NL routing (backward compat when flag is off)
        result = await _nl_route_hermes(text)

    return json.dumps(result, indent=2, default=str)


def create_hermes_a2a(hermes_daemon=None) -> FastAPI:  # noqa: F821
    health_fn = hermes_daemon.health_check if hermes_daemon else None
    return create_a2a_app(agent_card=HERMES_CARD, handler=handle_a2a, health_check=health_fn)
