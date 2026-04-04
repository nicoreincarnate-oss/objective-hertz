"""A2A server for the Ruflo engineering daemon."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from shared.a2a_wrapper import AgentCard, create_a2a_app

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("perseus.ruflo.a2a")


RUFLO_CARD = AgentCard(
    name="ruflo",
    description="Engineering execution daemon for code fixes, reviews, audits, and test generation.",
    url="http://localhost:9004",
    version="1.0.0",
    capabilities=[
        "code_fix",
        "code_review",
        "code_refactor",
        "security_scan",
        "dependency_audit",
        "implement_tool",
        "test_generate",
        "health_check",
    ],
)


async def _dispatch(daemon, capability: str, params: dict[str, Any]) -> dict[str, Any]:
    if daemon is None:
        return {"ok": False, "error": "ruflo daemon is unavailable"}
    return await daemon.handle_capability(capability, params)


async def handle_a2a_input(input_text: str, daemon=None) -> str:
    payload: dict[str, Any]
    try:
        parsed = json.loads(input_text)
        if isinstance(parsed, dict):
            payload = parsed
        else:
            payload = {"capability": "health_check", "params": {"value": parsed}}
    except json.JSONDecodeError:
        lowered = input_text.lower().strip()
        if "review" in lowered:
            payload = {"capability": "code_review", "params": {"description": input_text}}
        elif "security" in lowered or "audit" in lowered:
            payload = {"capability": "security_scan", "params": {"description": input_text}}
        elif "test" in lowered:
            payload = {"capability": "test_generate", "params": {"description": input_text}}
        elif "refactor" in lowered:
            payload = {"capability": "code_refactor", "params": {"description": input_text}}
        elif "fix" in lowered or "bug" in lowered:
            payload = {"capability": "code_fix", "params": {"description": input_text}}
        else:
            payload = {"capability": "health_check", "params": {}}

    capability = str(payload.get("capability", "health_check") or "health_check")
    params = payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {}
    result = await _dispatch(daemon, capability, params)
    return json.dumps(result, ensure_ascii=False, default=str)


def create_ruflo_a2a(daemon=None) -> FastAPI:  # noqa: F821
    async def handler(input_text: str) -> str:
        return await handle_a2a_input(input_text, daemon)

    health_fn = daemon.health_check if daemon is not None else None
    return create_a2a_app(agent_card=RUFLO_CARD, handler=handler, health_check=health_fn)


__all__ = ["RUFLO_CARD", "create_ruflo_a2a", "handle_a2a_input"]
