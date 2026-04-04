"""A2A server for the system executor daemon."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from shared.a2a_wrapper import AgentCard, create_a2a_app

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("perseus.system_executor.a2a")


SYSTEM_EXECUTOR_CARD = AgentCard(
    name="system_executor",
    description=(
        "System executor surface for desktop control, shell execution, system actions, "
        "auth checkpoints, and live screen context."
    ),
    url="http://localhost:9010",
    version="1.0.0",
    capabilities=[
        "desktop_control",
        "desktop_exec",
        "system_action",
        "auth_checkpoint",
        "screen_context",
        "health_check",
    ],
)


async def _dispatch(executor, capability: str, params: dict[str, Any]) -> dict[str, Any]:
    if executor is None:
        return {"ok": False, "error": "system executor is unavailable"}
    return await executor.handle_capability(capability, params)


async def handle_a2a_input(input_text: str, executor=None) -> str:
    """Parse an A2A task payload and return JSON-encoded output."""
    payload: dict[str, Any]
    try:
        parsed = json.loads(input_text)
        if isinstance(parsed, dict):
            payload = parsed
        else:
            payload = {"capability": "health_check", "params": {"value": parsed}}
    except json.JSONDecodeError:
        lowered = input_text.lower().strip()
        if "screen" in lowered or "context" in lowered or "screenshot" in lowered:
            payload = {"capability": "screen_context", "params": {}}
        elif "auth" in lowered or "checkpoint" in lowered or "approve" in lowered:
            payload = {"capability": "auth_checkpoint", "params": {"operation": "request", "purpose": input_text}}
        elif "exec" in lowered or lowered.startswith("!") or lowered.startswith("$"):
            payload = {"capability": "desktop_exec", "params": {"command": input_text}}
        elif "control" in lowered or "click" in lowered or "type" in lowered:
            payload = {"capability": "desktop_control", "params": {"action": lowered.split()[0] if lowered else "click"}}
        else:
            payload = {"capability": "health_check", "params": {}}

    capability = str(payload.get("capability", "health_check") or "health_check")
    params = payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {}
    result = await _dispatch(executor, capability, params)
    return json.dumps(result, ensure_ascii=False, default=str)


def create_system_executor_a2a(system_executor=None) -> FastAPI:  # noqa: F821
    async def handler(input_text: str) -> str:
        return await handle_a2a_input(input_text, system_executor)

    health_fn = system_executor.health_check if system_executor is not None else None
    return create_a2a_app(agent_card=SYSTEM_EXECUTOR_CARD, handler=handler, health_check=health_fn)


__all__ = ["SYSTEM_EXECUTOR_CARD", "create_system_executor_a2a", "handle_a2a_input"]
