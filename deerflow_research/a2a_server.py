"""A2A server for the DeerFlow-style research daemon."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from shared.a2a_wrapper import AgentCard, create_a2a_app

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("perseus.deerflow_research.a2a")


DEERFLOW_RESEARCH_CARD = AgentCard(
    name="deerflow_research",
    description=(
        "Continuous deep research daemon for open-ended evolution scouting, "
        "paper scanning, repo scanning, and daily improvement briefs."
    ),
    url="http://localhost:9011",
    version="1.0.0",
    capabilities=[
        "evolution_research_cycle",
        "paper_scan",
        "repo_scan",
        "daily_evolution_brief",
        "health_check",
    ],
)


async def _dispatch(daemon, capability: str, params: dict[str, Any]) -> dict[str, Any]:
    if daemon is None:
        return {"ok": False, "error": "deerflow_research daemon is unavailable"}
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
        if "github" in lowered or "repo" in lowered:
            payload = {"capability": "repo_scan", "params": {"topic": input_text}}
        elif "paper" in lowered or "arxiv" in lowered or "stanford" in lowered:
            payload = {"capability": "paper_scan", "params": {"topic": input_text}}
        elif "daily" in lowered or "brief" in lowered or "24 hours" in lowered:
            payload = {"capability": "daily_evolution_brief", "params": {"topic": input_text}}
        elif "health" in lowered or "status" in lowered:
            payload = {"capability": "health_check", "params": {}}
        else:
            payload = {"capability": "evolution_research_cycle", "params": {"topic": input_text}}

    capability = str(payload.get("capability", "health_check") or "health_check")
    params = payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {}
    result = await _dispatch(daemon, capability, params)
    return json.dumps(result, ensure_ascii=False, default=str)


def create_deerflow_research_a2a(daemon=None) -> FastAPI:  # noqa: F821
    async def handler(input_text: str) -> str:
        return await handle_a2a_input(input_text, daemon)

    health_fn = daemon.health_check if daemon is not None else None
    return create_a2a_app(agent_card=DEERFLOW_RESEARCH_CARD, handler=handler, health_check=health_fn)


__all__ = ["DEERFLOW_RESEARCH_CARD", "create_deerflow_research_a2a", "handle_a2a_input"]
