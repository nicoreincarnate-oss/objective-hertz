"""
Pipeline DAG — Constraint-aware pipeline transition system (NS-Mem paper).

Encodes the 10-stage revenue pipeline as a directed acyclic graph with
constraints at each node. Unlike the simple state_machine.py which just
validates transitions (bool), this returns WHY a transition is allowed/blocked
and WHAT inputs are required.

Also provides entity-centric timeline queries (M2A/M3-Agent papers):
"Show me everything that happened with this client in temporal order."

Gated behind PIPELINE_DAG_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("perseus.pipeline_dag")

PIPELINE_DAG_ENABLED = os.environ.get("PIPELINE_DAG_ENABLED", "1") == "1"


# ── Stage Definitions ────────────────────────────────────────────────

@dataclass(frozen=True)
class StageConstraint:
    """What must be true before entering this stage."""
    required_fields: list[str] = field(default_factory=list)  # client fields that must be non-empty
    required_status: list[str] = field(default_factory=list)  # previous statuses that allow entry
    budget_check: bool = False  # must check budget before proceeding
    compliance_check: bool = False  # must pass compliance before proceeding
    human_approval: bool = False  # requires review queue approval
    min_lead_score: float = 0.0  # minimum lead score to enter


@dataclass(frozen=True)
class StageDefinition:
    """Complete definition of a pipeline stage."""
    name: str
    description: str
    constraints: StageConstraint
    outputs: list[str]  # what this stage produces (client fields written)
    next_stages: list[str]  # valid transitions from here
    handler_module: str  # which pipeline module handles this


# The 10-stage pipeline encoded as a DAG
STAGES: dict[str, StageDefinition] = {
    "discovered": StageDefinition(
        name="discovered",
        description="Business found without website. Raw lead.",
        constraints=StageConstraint(required_fields=[], required_status=[]),
        outputs=["business_name", "city", "region", "source"],
        next_stages=["researched", "lost"],
        handler_module="titan.pipeline.lead_discovery",
    ),
    "researched": StageDefinition(
        name="researched",
        description="Lead enriched with email, industry, decision-maker info.",
        constraints=StageConstraint(
            required_fields=["business_name"],
            required_status=["discovered"],
        ),
        outputs=["email", "industry", "research_facts", "research_graph"],
        next_stages=["email_drafted", "lost"],
        handler_module="titan.pipeline.lead_research",
    ),
    "email_drafted": StageDefinition(
        name="email_drafted",
        description="Personalized outreach email composed.",
        constraints=StageConstraint(
            required_fields=["email", "research_facts"],
            required_status=["researched"],
        ),
        outputs=["email_draft"],
        next_stages=["email_sent", "lost"],
        handler_module="titan.pipeline.email_compose",
    ),
    "email_sent": StageDefinition(
        name="email_sent",
        description="Email sent via Instantly.ai campaign.",
        constraints=StageConstraint(
            required_fields=["email", "email_draft"],
            required_status=["email_drafted", "email_queued"],
            budget_check=True,
        ),
        outputs=["instantly_campaign_id"],
        next_stages=["followed_up", "replied", "interested", "lost"],
        handler_module="titan.pipeline.email_send",
    ),
    "interested": StageDefinition(
        name="interested",
        description="Lead replied with positive interest.",
        constraints=StageConstraint(
            required_fields=["email"],
            required_status=["email_sent", "followed_up", "replied"],
        ),
        outputs=[],
        next_stages=["demo_built", "lost"],
        handler_module="titan.pipeline.follow_up",
    ),
    "demo_built": StageDefinition(
        name="demo_built",
        description="Preview website built and deployed to Netlify.",
        constraints=StageConstraint(
            required_fields=["research_facts"],
            required_status=["interested"],
            budget_check=True,
        ),
        outputs=["demo_site_url"],
        next_stages=["proposal_sent", "lost"],
        handler_module="titan.pipeline.build_site",
    ),
    "proposal_sent": StageDefinition(
        name="proposal_sent",
        description="Pricing proposal sent to prospect.",
        constraints=StageConstraint(
            required_fields=["demo_site_url", "email"],
            required_status=["demo_built"],
        ),
        outputs=[],
        next_stages=["negotiating", "closed", "lost"],
        handler_module="titan.pipeline.close_deal",
    ),
    "closed": StageDefinition(
        name="closed",
        description="Deal closed. Payment terms agreed.",
        constraints=StageConstraint(
            required_fields=["demo_site_url"],
            required_status=["proposal_sent", "negotiating"],
            human_approval=True,  # first 10 sales require approval
            compliance_check=True,
        ),
        outputs=["deal_amount"],
        next_stages=["building"],
        handler_module="titan.pipeline.close_deal",
    ),
    "deployed": StageDefinition(
        name="deployed",
        description="Production website live with custom domain.",
        constraints=StageConstraint(
            required_fields=["deal_amount"],
            required_status=["building"],
            budget_check=True,
        ),
        outputs=["final_site_url", "hosting_subscription_id"],
        next_stages=["invoiced"],
        handler_module="titan.pipeline.deploy_site",
    ),
    "paid": StageDefinition(
        name="paid",
        description="Payment received. Deal complete.",
        constraints=StageConstraint(
            required_fields=["final_site_url"],
            required_status=["invoiced"],
        ),
        outputs=["payment_reference"],
        next_stages=[],  # terminal
        handler_module="titan.pipeline.invoice",
    ),
}

# Additional intermediate states (pass-through)
PASSTHROUGH_STAGES = {
    "email_queued": ["email_sent"],
    "followed_up": ["replied", "interested", "lost"],
    "replied": ["interested", "lost"],
    "negotiating": ["closed", "lost"],
    "building": ["deployed"],
    "invoiced": ["paid"],
}


# ── DAG Queries ──────────────────────────────────────────────────────

def can_transition(
    current_stage: str,
    target_stage: str,
    client_data: dict | None = None,
) -> dict:
    """Check if a stage transition is valid and return detailed reasons.

    Returns:
        {
            "allowed": bool,
            "reasons": ["why allowed/blocked"],
            "missing_fields": ["field1", "field2"],
            "required_checks": ["budget", "compliance", "approval"],
        }

    Unlike state_machine.can_transition() which returns bool, this
    tells you WHY and WHAT'S MISSING (NS-Mem paper constraint-awareness).
    """
    if not PIPELINE_DAG_ENABLED:
        # Fallback to simple yes/no
        return {"allowed": True, "reasons": ["DAG disabled — using simple state machine"], "missing_fields": [], "required_checks": []}

    result = {"allowed": True, "reasons": [], "missing_fields": [], "required_checks": []}
    client = client_data or {}

    # Terminal states
    if target_stage in ("lost", "churned"):
        result["reasons"].append(f"Terminal state '{target_stage}' always allowed")
        return result

    # Check if target stage exists
    stage_def = STAGES.get(target_stage)
    if not stage_def:
        # Check passthrough
        if target_stage in PASSTHROUGH_STAGES:
            result["reasons"].append(f"Passthrough stage '{target_stage}'")
            return result
        result["allowed"] = False
        result["reasons"].append(f"Unknown stage: {target_stage}")
        return result

    # Check required previous status
    if stage_def.constraints.required_status:
        if current_stage not in stage_def.constraints.required_status:
            result["allowed"] = False
            result["reasons"].append(
                f"Cannot transition from '{current_stage}' to '{target_stage}'. "
                f"Required: {stage_def.constraints.required_status}"
            )

    # Check required fields on client
    for field_name in stage_def.constraints.required_fields:
        val = client.get(field_name)
        if not val or (isinstance(val, str) and not val.strip()):
            result["allowed"] = False
            result["missing_fields"].append(field_name)

    if result["missing_fields"]:
        result["reasons"].append(f"Missing required fields: {result['missing_fields']}")

    # Flag required checks (caller must perform these)
    if stage_def.constraints.budget_check:
        result["required_checks"].append("budget")
    if stage_def.constraints.compliance_check:
        result["required_checks"].append("compliance")
    if stage_def.constraints.human_approval:
        result["required_checks"].append("human_approval")

    if not result["reasons"]:
        result["reasons"].append(f"All constraints satisfied for '{target_stage}'")

    return result


def get_stage_constraints(stage: str) -> dict | None:
    """Get the full constraint definition for a stage."""
    stage_def = STAGES.get(stage)
    if not stage_def:
        return None
    return {
        "name": stage_def.name,
        "description": stage_def.description,
        "required_fields": stage_def.constraints.required_fields,
        "required_status": stage_def.constraints.required_status,
        "budget_check": stage_def.constraints.budget_check,
        "compliance_check": stage_def.constraints.compliance_check,
        "human_approval": stage_def.constraints.human_approval,
        "outputs": stage_def.outputs,
        "next_stages": stage_def.next_stages,
        "handler_module": stage_def.handler_module,
    }


def get_pipeline_path(from_stage: str, to_stage: str) -> list[str] | None:
    """Find the shortest valid path between two stages. BFS traversal."""
    if from_stage == to_stage:
        return [from_stage]

    visited = set()
    queue = [(from_stage, [from_stage])]

    while queue:
        current, path = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        # Get next stages from both STAGES and PASSTHROUGH_STAGES
        next_stages = []
        if current in STAGES:
            next_stages = STAGES[current].next_stages
        elif current in PASSTHROUGH_STAGES:
            next_stages = PASSTHROUGH_STAGES[current]

        for next_s in next_stages:
            if next_s == to_stage:
                return path + [next_s]
            if next_s not in visited:
                queue.append((next_s, path + [next_s]))

    return None  # no valid path


def validate_pipeline_state(client_data: dict) -> list[str]:
    """Audit a client's current state against DAG constraints.

    Returns list of violations (empty if clean).
    """
    violations = []
    status = client_data.get("status", "")

    if not status:
        violations.append("Client has no status")
        return violations

    stage_def = STAGES.get(status)
    if not stage_def and status not in PASSTHROUGH_STAGES and status not in ("lost", "churned"):
        violations.append(f"Unknown status: {status}")
        return violations

    if stage_def:
        for field_name in stage_def.constraints.required_fields:
            val = client_data.get(field_name)
            if not val or (isinstance(val, str) and not val.strip()):
                violations.append(f"Stage '{status}' requires '{field_name}' but it's missing")

    return violations


# ── Entity Timeline (M2A/M3-Agent papers) ────────────────────────────

async def entity_timeline(entity_name: str, limit: int = 50) -> list[dict]:
    """Return all events for an entity in temporal order.

    Queries Neo4j INVOLVES edges to find all MemoryNodes connected
    to this entity, sorted by timestamp. This is the M2A paper's
    "evidence linking" — every fact about a client/industry is traceable.
    """
    if not PIPELINE_DAG_ENABLED:
        return []

    try:
        from shared.magma import _get_driver
        driver = _get_driver()
        if not driver:
            return []

        with driver.session() as session:
            results = session.run(
                """MATCH (e:Entity {name: $name})<-[:INVOLVES]-(n:MemoryNode)
                   RETURN n.node_id as node_id, n.content as content,
                          n.category as category, n.timestamp as timestamp
                   ORDER BY n.timestamp ASC LIMIT $limit""",
                name=entity_name.lower().strip(),
                limit=limit,
            )
            return [
                {
                    "node_id": r["node_id"],
                    "content": r["content"],
                    "category": r["category"],
                    "timestamp": r["timestamp"],
                }
                for r in results
            ]
    except Exception as e:
        logger.debug(f"Entity timeline query failed: {e}")
        return []
