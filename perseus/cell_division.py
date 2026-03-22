"""Cell division — the system proposes new specialized agents when needed.

v1: Proposal-only. The system detects sustained bottlenecks and proposes
a new agent. Nico must approve before ClawdBot builds it.

This is the "stem cell" stage — the capability exists but is gated on
operator consent. The system will never autonomously spawn agents.
"""

import json
import logging
from typing import Any

from shared.comms import record_decision
from shared.db import emit_event, fetch_val

logger = logging.getLogger("perseus.cell_division")

# Thresholds that trigger a cell division proposal
STAGE_ERROR_RATE_THRESHOLD = 0.20  # >20% error rate on a pipeline stage
AGENT_ERROR_RATE_THRESHOLD = 0.10  # >10% error rate on an agent
REVENUE_CONCENTRATION_THRESHOLD = 0.50  # >50% revenue from one region/industry
MIN_DAYS_BEFORE_PROPOSAL = 7  # Need 7+ days of data before proposing


async def evaluate_division_need(snapshot: dict, cycle_id: int) -> None:
    """Check if the system needs a new specialized agent.

    Called by the sleep cycle after Phase C. Only proposes — never creates.
    """
    proposals = []

    # Check: sustained pipeline stage errors
    errors = snapshot.get("recent_errors", [])
    if errors:
        stage_errors: dict[str, int] = {}
        for e in errors:
            stage = e.get("stage", "unknown") if isinstance(e, dict) else "unknown"
            stage_errors[stage] = stage_errors.get(stage, 0) + 1

        for stage, count in stage_errors.items():
            if count >= 5:  # 5+ errors in one stage in 24h
                proposals.append({
                    "name": f"{stage}-specialist",
                    "reason": (
                        f"Pipeline stage '{stage}' had {count} errors in 24h. "
                        f"A dedicated agent could handle this stage with specialized logic."
                    ),
                    "trigger": "stage_error_rate",
                    "data": {"stage": stage, "error_count": count},
                })

    # Check: revenue concentration in one region/industry
    source_quality = snapshot.get("source_quality", [])
    total_revenue = sum(float(s.get("revenue", 0)) for s in source_quality)
    if total_revenue > 0:
        for source in source_quality:
            source_revenue = float(source.get("revenue", 0))
            if source_revenue / total_revenue > REVENUE_CONCENTRATION_THRESHOLD:
                source_name = source.get("source", "unknown")
                proposals.append({
                    "name": f"{source_name}-outreach-agent",
                    "reason": (
                        f"Source '{source_name}' generates {source_revenue/total_revenue:.0%} of revenue. "
                        f"A dedicated agent with specialized prompts and targeting could optimize this channel."
                    ),
                    "trigger": "revenue_concentration",
                    "data": {"source": source_name, "revenue_share": source_revenue / total_revenue},
                })

    # Check: agent self-model shows sustained weakness
    self_models = snapshot.get("agent_self_models", {})
    for agent_name, model in self_models.items():
        error_rate = model.get("error_rate_24h", 0)
        if error_rate > AGENT_ERROR_RATE_THRESHOLD:
            worst_stage = model.get("worst_performing_stage", "")
            if worst_stage:
                proposals.append({
                    "name": f"{worst_stage}-offload-agent",
                    "reason": (
                        f"Agent '{agent_name}' has {error_rate:.0%} error rate, "
                        f"worst at '{worst_stage}'. Offloading that stage to a dedicated agent "
                        f"could reduce errors and free capacity."
                    ),
                    "trigger": "agent_overload",
                    "data": {"agent": agent_name, "error_rate": error_rate, "stage": worst_stage},
                })

    # Dedupe and limit
    seen_names = set()
    unique_proposals = []
    for p in proposals:
        if p["name"] not in seen_names:
            seen_names.add(p["name"])
            unique_proposals.append(p)

    # Only propose if we haven't recently proposed the same thing
    for proposal in unique_proposals[:2]:
        already_proposed = await fetch_val(
            """SELECT COUNT(*) FROM agent_decisions
               WHERE agent = 'sleep_cycle'
               AND decision_type = 'cell_division_proposal'
               AND decision->>'name' = %s
               AND created_at > NOW() - INTERVAL '7 days'""",
            (proposal["name"],),
        )
        if already_proposed and already_proposed > 0:
            continue

        await record_decision(
            agent="sleep_cycle",
            decision_type="cell_division_proposal",
            context={"cycle_id": cycle_id, "trigger": proposal["trigger"]},
            decision=proposal,
            reasoning=proposal["reason"],
        )

        await emit_event("cell_division_proposed", {
            "name": proposal["name"],
            "reason": proposal["reason"],
            "trigger": proposal["trigger"],
            "cycle_id": cycle_id,
        })

        logger.info(f"Cell division proposed: {proposal['name']} — {proposal['reason'][:80]}")


async def propose_new_agent(
    name: str,
    reason: str,
    specialization: str,
    based_on: str = "titan",
) -> int | None:
    """Manually propose a new agent (callable from the War Room or sleep cycle).

    Returns the decision ID, or None if blocked.
    """
    decision_id = await record_decision(
        agent="operator",
        decision_type="cell_division_proposal",
        context={"based_on": based_on, "specialization": specialization},
        decision={"name": name, "reason": reason, "based_on": based_on},
        reasoning=reason,
    )

    await emit_event("cell_division_proposed", {
        "name": name,
        "reason": reason,
        "based_on": based_on,
        "specialization": specialization,
        "source": "operator",
    })

    return decision_id
