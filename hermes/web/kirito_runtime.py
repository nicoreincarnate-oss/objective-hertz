"""Shared runtime helpers for Kirito command planning and status views."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping


LIFECYCLE_ORDER: dict[str, int] = {
    "kirito_command_received": 10,
    "kirito_command_classified": 20,
    "kirito_command_planned": 30,
    "kirito_command_dispatched": 40,
    "executor_tool_started": 50,
    "executor_auth_wait": 60,
    "executor_step_progress": 70,
    "executor_tool_finished": 80,
    "kirito_command_completed": 90,
    "kirito_command_failed": 100,
}


def serialize_route_plan(plan: Any) -> dict[str, Any]:
    """Return a JSON-safe route plan payload with backward-compatible defaults."""
    if is_dataclass(plan):
        raw = asdict(plan)
    elif isinstance(plan, Mapping):
        raw = dict(plan)
    else:
        raw = {
            key: getattr(plan, key)
            for key in dir(plan)
            if not key.startswith("_") and not callable(getattr(plan, key))
        }

    payload = {
        "intent": raw.get("intent"),
        "task_type": raw.get("task_type"),
        "capability": raw.get("capability"),
        "target_agent": raw.get("target_agent"),
        "local_action": bool(raw.get("local_action")),
        "requires_followup": bool(raw.get("requires_followup")),
        "followup_question": raw.get("followup_question"),
        "payload": raw.get("payload") or {},
        "signals": list(raw.get("signals") or []),
        "confidence": raw.get("confidence", 0.0),
        "rationale": raw.get("rationale", ""),
        "domain": raw.get("domain", _infer_domain(raw)),
        "executor": raw.get("executor", raw.get("target_agent")),
        "executor_kind": raw.get("executor_kind", _infer_executor_kind(raw)),
        "risk": raw.get("risk", _infer_risk(raw)),
        "risk_class": raw.get("risk_class", raw.get("risk", _infer_risk(raw))),
        "supports_auth": bool(raw.get("supports_auth", False)),
        "supports_full_autonomy": bool(raw.get("supports_full_autonomy", True)),
        "visibility_mode": raw.get("visibility_mode", "event_stream"),
        "fallback_executor": raw.get("fallback_executor"),
        "capability_family": raw.get("capability_family", _infer_capability_family(raw)),
    }
    return payload


def normalize_dispatch_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Translate broad planner intents into concrete executor capabilities."""
    normalized = dict(plan)
    intent = str(normalized.get("intent", ""))
    source_text = str((normalized.get("payload") or {}).get("source_text", "")).lower()

    if intent == "desktop_task":
        normalized.update(
            {
                "target_agent": "system_executor",
                "executor": "system_executor",
                "executor_kind": "a2a_capability",
                "task_type": "desktop_control",
                "capability": "desktop_control",
                "capability_family": "desktop.*",
            }
        )
    elif intent == "system_task":
        normalized.update(
            {
                "target_agent": "system_executor",
                "executor": "system_executor",
                "executor_kind": "a2a_capability",
                "task_type": "system_action",
                "capability": "system_action",
                "capability_family": "system.*",
            }
        )
    elif intent == "browser_task":
        normalized.update(
            {
                "target_agent": "clawdbot",
                "executor": "clawdbot",
                "task_type": "browser_flow",
                "capability": "browser_flow",
                "capability_family": "browser.*",
            }
        )
    elif intent == "workflow_task":
        normalized.update(
            {
                "target_agent": "clawdbot",
                "executor": "clawdbot",
                "executor_kind": "a2a_capability",
                "task_type": "workflow_run",
                "capability": "workflow_run",
                "capability_family": "workflow.*",
                "supports_auth": True,
            }
        )
    elif intent in {"research_and_apply", "evolution_research"}:
        normalized.update(
            {
                "target_agent": "deerflow_research",
                "executor": "deerflow_research",
                "executor_kind": "a2a_capability",
                "task_type": "evolution_research_cycle",
                "capability": "evolution_research_cycle",
                "capability_family": "research.*",
                "supports_auth": False,
                "supports_full_autonomy": True,
            }
        )
    elif intent == "repo_task":
        normalized.update(
            {
                "target_agent": "ruflo",
                "executor": "ruflo",
                "task_type": "code_fix",
                "capability": "code_fix",
                "capability_family": "repo.*",
            }
        )
    elif intent == "observe_task" and any(term in source_text for term in ("screen", "desktop", "window", "history", "ocr")):
        normalized.update(
            {
                "target_agent": "system_executor",
                "executor": "system_executor",
                "executor_kind": "a2a_capability",
                "task_type": "screen_context",
                "capability": "screen_context",
                "capability_family": "observe.*",
            }
        )

    return normalized


def build_status_graph(
    *,
    events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a richer command status graph from event/task rows."""
    normalized_events = sorted(
        events,
        key=lambda event: (
            LIFECYCLE_ORDER.get(str(event.get("event_type", "")), 999),
            str(event.get("created_at") or ""),
        ),
    )
    steps: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    auth_waits: list[dict[str, Any]] = []
    state = "dispatched" if normalized_events else "idle"

    for event in normalized_events:
        payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("event_type", ""))
        step = {
            "event_type": event_type,
            "label": event_type.replace("_", " "),
            "created_at": event.get("created_at"),
            "payload": payload,
        }
        steps.append(step)

        if event_type == "kirito_command_failed":
            state = "failed"
        elif event_type == "kirito_command_completed":
            state = "completed"
        elif event_type == "executor_auth_wait":
            state = "auth_wait"
            auth_waits.append(
                {
                    "channel": payload.get("channel") or payload.get("provider") or "auth",
                    "reason": payload.get("reason") or payload.get("message") or "authentication required",
                    "created_at": event.get("created_at"),
                }
            )
        elif event_type in {"executor_tool_started", "executor_step_progress"} and state not in {"failed", "completed", "auth_wait"}:
            state = "running"

        if event_type in {"kirito_command_planned", "kirito_command_dispatched", "executor_tool_started", "executor_tool_finished"}:
            spans.append(
                {
                    "event_type": event_type,
                    "executor": payload.get("selected_executor") or payload.get("executor") or payload.get("target_agent"),
                    "target_agent": payload.get("target_agent"),
                    "task_type": payload.get("task_type"),
                    "capability": payload.get("capability"),
                    "status": payload.get("status") or payload.get("dispatch_mode") or event_type,
                    "created_at": event.get("created_at"),
                }
            )

        if payload.get("artifact"):
            artifacts.append(payload["artifact"])
        if payload.get("artifacts") and isinstance(payload["artifacts"], list):
            artifacts.extend(item for item in payload["artifacts"] if isinstance(item, dict))
        if payload.get("replay_url"):
            artifacts.append({"type": "replay", "url": payload["replay_url"]})

    for task in tasks:
        task_status = str(task.get("status") or "")
        if task_status in {"working", "running"} and state not in {"failed", "completed", "auth_wait"}:
            state = "running"
        spans.append(
            {
                "event_type": "task_queue",
                "executor": task.get("payload", {}).get("delegated_to") if isinstance(task.get("payload"), dict) else None,
                "target_agent": task.get("payload", {}).get("target_agent") if isinstance(task.get("payload"), dict) else None,
                "task_type": task.get("task_type"),
                "capability": None,
                "status": task_status,
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
                "task_id": task.get("id"),
            }
        )

    return {
        "state": state,
        "steps": steps,
        "spans": spans,
        "artifacts": artifacts,
        "auth_waits": auth_waits,
    }


def _infer_domain(raw: Mapping[str, Any]) -> str:
    intent = str(raw.get("intent", ""))
    if intent == "open_dashboard":
        return "desktop"
    if "browser" in intent:
        return "browser"
    if "code" in intent or "repo" in intent:
        return "repo"
    if "memory" in intent:
        return "memory"
    if "workflow" in intent:
        return "workflow"
    return "system"


def _infer_executor_kind(raw: Mapping[str, Any]) -> str:
    if raw.get("local_action"):
        return "local_action"
    capability = str(raw.get("capability", ""))
    if capability in {"operator_command", "ask"}:
        return "a2a_capability"
    return "task_queue"


def _infer_risk(raw: Mapping[str, Any]) -> str:
    if raw.get("local_action"):
        return "low"
    intent = str(raw.get("intent", ""))
    if "browser" in intent or "workflow" in intent:
        return "medium"
    if "code" in intent or "system" in intent:
        return "high"
    return "medium"


def _infer_capability_family(raw: Mapping[str, Any]) -> str:
    domain = _infer_domain(raw)
    return f"{domain}.*"
