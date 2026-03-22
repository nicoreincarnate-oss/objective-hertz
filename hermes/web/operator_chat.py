"""Operator-to-agent messaging helpers for the Hermes dashboard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

TARGET_AGENTS = {
    "perseus": {
        "label": "Perseus",
        "description": "Master orchestrator and strategic coordinator.",
        "task_type": "perseus_operator_message",
    },
    "titan": {
        "label": "Titan",
        "description": "Revenue engine, pipeline movement, and close work.",
        "task_type": "titan_operator_message",
    },
    "clawdbot": {
        "label": "ClawdBot",
        "description": "Skills, scraping, enrichment, and verification work.",
        "task_type": "clawdbot_operator_message",
    },
}

PRIORITY_VALUES = {
    "urgent": 1,
    "priority": 3,
    "routine": 5,
}


def get_agent_options() -> list[dict[str, str]]:
    """Return operator-facing agent options for the dashboard composer."""
    return [
        {"value": key, **value}
        for key, value in TARGET_AGENTS.items()
    ]


def create_operator_dispatch(target_agent: str, message: str, priority: str) -> dict[str, Any]:
    """Validate and normalize an operator message dispatch."""
    normalized_agent = target_agent.strip().lower()
    normalized_message = " ".join(message.strip().split())
    normalized_priority = priority.strip().lower()

    if normalized_agent not in TARGET_AGENTS:
        raise ValueError("Choose a supported target agent.")
    if not normalized_message:
        raise ValueError("Message cannot be empty.")
    if normalized_priority not in PRIORITY_VALUES:
        raise ValueError("Choose a supported priority.")

    return {
        "target_agent": normalized_agent,
        "task_type": TARGET_AGENTS[normalized_agent]["task_type"],
        "priority": normalized_priority,
        "priority_value": PRIORITY_VALUES[normalized_priority],
        "payload": {
            "target_agent": normalized_agent,
            "message": normalized_message,
            "priority": normalized_priority,
            "source": "war_room",
        },
    }


def build_operator_thread(events: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Extract operator-message events into a compact conversation thread."""
    thread: list[dict[str, str]] = []
    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event_type == "operator_message_sent":
            agent_key = str(payload.get("target_agent", "")).lower()
            agent = TARGET_AGENTS.get(agent_key, {}).get("label", agent_key.title() or "Agent")
            thread.append(
                {
                    "time": _format_event_time(event.get("created_at")),
                    "eyebrow": f"You -> {agent}",
                    "title": "Message queued",
                    "detail": str(payload.get("message", "")),
                    "tone": _priority_tone(str(payload.get("priority", "routine"))),
                }
            )
        elif event_type == "agent_message_ack":
            agent = str(payload.get("agent", "agent")).title()
            reply = str(payload.get("reply", "")).strip() or "Agent acknowledged your note."
            operator_message = str(payload.get("operator_message", "")).strip()
            detail = reply if not operator_message else f"{reply} Last note: {operator_message}"
            thread.append(
                {
                    "time": _format_event_time(event.get("created_at")),
                    "eyebrow": agent,
                    "title": "Acknowledged",
                    "detail": detail,
                    "tone": "success",
                }
            )
    return thread[:6]


def _priority_tone(priority: str) -> str:
    if priority == "urgent":
        return "warning"
    if priority == "priority":
        return "default"
    return "default"


def _format_event_time(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return "Now"
