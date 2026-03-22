"""Per-agent self-model — each agent knows its own strengths and weaknesses.

Updated nightly by the sleep cycle. Read by:
- Perseus (route work to the best agent)
- ClawdBot brain (Opus sees what it's good/bad at)
- Sleep cycle Alpha/Beta (observe all agents' self-awareness)
- Hermes (weekly summary to Nico)
"""

import logging
from datetime import datetime
from typing import Any

from shared.db import get_config, set_config, fetch_all, fetch_val

logger = logging.getLogger("perseus.self_model")

_CONFIG_PREFIX = "self_model_"


async def get_self_model(agent_name: str) -> dict[str, Any]:
    """Read an agent's self-assessment from system_config."""
    model = await get_config(f"{_CONFIG_PREFIX}{agent_name}", {})
    if not isinstance(model, dict):
        model = {}
    return {
        "agent": agent_name,
        "strengths": model.get("strengths", []),
        "weaknesses": model.get("weaknesses", []),
        "improving": model.get("improving", []),
        "error_rate_24h": model.get("error_rate_24h", 0.0),
        "decisions_made_24h": model.get("decisions_made_24h", 0),
        "top_performing_stage": model.get("top_performing_stage", ""),
        "worst_performing_stage": model.get("worst_performing_stage", ""),
        "last_updated": model.get("last_updated", ""),
    }


async def update_self_model(agent_name: str, updates: dict[str, Any]) -> None:
    """Update an agent's self-assessment."""
    current = await get_self_model(agent_name)
    current.update(updates)
    current["last_updated"] = datetime.now().isoformat()
    await set_config(f"{_CONFIG_PREFIX}{agent_name}", current)
    logger.debug(f"Updated self-model for {agent_name}")


async def get_all_self_models() -> dict[str, dict[str, Any]]:
    """Read all agents' self-models."""
    agents = ["perseus", "titan", "hermes", "clawdbot"]
    return {name: await get_self_model(name) for name in agents}


async def format_self_model_for_prompt(agent_name: str) -> str:
    """Format a self-model as text for injection into LLM prompts."""
    model = await get_self_model(agent_name)
    if not model.get("last_updated"):
        return f"[{agent_name}: no self-assessment yet]"

    lines = [f"SELF-ASSESSMENT ({agent_name}):"]
    if model["strengths"]:
        lines.append(f"  Strengths: {', '.join(model['strengths'][:5])}")
    if model["weaknesses"]:
        lines.append(f"  Weaknesses: {', '.join(model['weaknesses'][:5])}")
    if model["improving"]:
        lines.append(f"  Improving: {', '.join(model['improving'][:3])}")
    if model["error_rate_24h"]:
        lines.append(f"  Error rate (24h): {model['error_rate_24h']:.1%}")
    if model["top_performing_stage"]:
        lines.append(f"  Best at: {model['top_performing_stage']}")
    if model["worst_performing_stage"]:
        lines.append(f"  Weakest: {model['worst_performing_stage']}")
    return "\n".join(lines)


async def format_all_self_models() -> str:
    """Format all agents' self-models for the sleep cycle."""
    models = await get_all_self_models()
    parts = []
    for name, model in models.items():
        parts.append(await format_self_model_for_prompt(name))
    return "\n\n".join(parts)


async def compute_agent_metrics(agent_name: str) -> dict[str, Any]:
    """Compute objective metrics for an agent from the last 24 hours.

    Called by the sleep cycle to build the self-model from real data.
    """
    # Decisions made
    decisions_24h = await fetch_val(
        """SELECT COUNT(*) FROM agent_decisions
           WHERE agent = %s AND created_at > NOW() - INTERVAL '24 hours'""",
        (agent_name,),
    ) or 0

    # Errors (tasks failed)
    errors_24h = await fetch_val(
        """SELECT COUNT(*) FROM task_queue
           WHERE assigned_agent = %s AND status = 'failed'
           AND completed_at > NOW() - INTERVAL '24 hours'""",
        (agent_name,),
    ) or 0

    # Tasks completed
    completed_24h = await fetch_val(
        """SELECT COUNT(*) FROM task_queue
           WHERE assigned_agent = %s AND status = 'completed'
           AND completed_at > NOW() - INTERVAL '24 hours'""",
        (agent_name,),
    ) or 0

    total = completed_24h + errors_24h
    error_rate = errors_24h / total if total > 0 else 0.0

    # Per-stage performance (for Titan)
    stage_errors = []
    if agent_name == "titan":
        stage_errors = await fetch_all(
            """SELECT payload->>'stage' as stage, COUNT(*) as cnt
               FROM events
               WHERE event_type = 'pipeline_error'
               AND created_at > NOW() - INTERVAL '24 hours'
               GROUP BY payload->>'stage'
               ORDER BY cnt DESC LIMIT 5"""
        )

    worst_stage = stage_errors[0]["stage"] if stage_errors else ""

    # Per-stage success (for Titan)
    stage_success = []
    if agent_name == "titan":
        stage_success = await fetch_all(
            """SELECT task_type as stage, COUNT(*) as cnt
               FROM task_queue
               WHERE assigned_agent = 'titan' AND status = 'completed'
               AND completed_at > NOW() - INTERVAL '24 hours'
               GROUP BY task_type
               ORDER BY cnt DESC LIMIT 5"""
        )

    best_stage = stage_success[0]["stage"] if stage_success else ""

    return {
        "decisions_made_24h": decisions_24h,
        "error_rate_24h": round(error_rate, 4),
        "tasks_completed_24h": completed_24h,
        "tasks_failed_24h": errors_24h,
        "top_performing_stage": best_stage,
        "worst_performing_stage": worst_stage,
    }
