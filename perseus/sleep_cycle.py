"""
Perseus Sleep Cycle — nightly self-optimization via contrarian Opus debate.

Every night (after daily_reflection), two Opus personas review the entire system:
- Alpha proposes changes based on observed errors and metric trends
- Beta argues the other side, challenging every proposal
- Surviving proposals are applied as backprop edits to soul docs, prompts, rules

This mirrors the neural network training loop:
- Forward pass = the day's pipeline execution
- Loss = measured outcomes (reply rate, conversion rate, revenue)
- Backpropagation = Alpha/Beta edit the behavioral files (the "weights")
- Learning rate = max 5 proposals, ±$50 pricing, confidence threshold
- Regularization = Beta prevents overfitting to one day's data
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from shared.comms import record_decision
from shared.config import config
from shared.db import emit_event, execute, fetch_all, fetch_one, fetch_val
from shared.llm_client import llm
from shared.self_model import (
    compute_agent_metrics,
    format_all_self_models,
    get_all_self_models,
    update_self_model,
)

logger = logging.getLogger("perseus.sleep_cycle")


async def run_sleep_cycle() -> dict[str, Any]:
    """Execute the full nightly sleep cycle. Called by Titan's task handler."""
    logger.info("=== SLEEP CYCLE STARTING ===")

    # Phase A: gather everything
    snapshot = await _gather_system_snapshot()

    # Phase B: contrarian debate
    alpha_proposals = await _run_alpha(snapshot)
    beta_verdicts = await _run_beta(alpha_proposals, snapshot)

    # Filter to surviving proposals
    surviving = _filter_surviving(alpha_proposals, beta_verdicts)
    logger.info(f"Sleep cycle: {len(alpha_proposals)} proposals → {len(surviving)} survived Beta review")

    # Log the cycle
    cycle_id = await _log_cycle(snapshot, alpha_proposals, beta_verdicts, surviving)

    # Phase C: apply changes
    applied = await _apply_changes(surviving, cycle_id)

    # Update agent self-models
    await _update_all_self_models(snapshot)

    # Check if cell division is warranted
    await _check_cell_division(snapshot, cycle_id)

    # Git commit (best-effort)
    if applied:
        from perseus.backprop import git_commit_cycle
        summary = "; ".join(p.get("what", "")[:50] for p in surviving[:3])
        await git_commit_cycle(cycle_id, summary)

    # Alert Nico
    await emit_event("sleep_cycle_complete", {
        "cycle_id": cycle_id,
        "proposals": len(alpha_proposals),
        "survived": len(surviving),
        "applied": applied,
        "top_change": surviving[0].get("what", "none") if surviving else "no changes",
    })

    logger.info(f"=== SLEEP CYCLE COMPLETE: {applied} changes applied ===")
    return {
        "cycle_id": cycle_id,
        "proposals": len(alpha_proposals),
        "survived": len(surviving),
        "applied": applied,
    }


# ── Phase A: Gather System Snapshot ───────────────────────────────

async def _gather_system_snapshot() -> dict[str, Any]:
    """Build a comprehensive snapshot of the entire system state."""
    from perseus.daemon import _assess_pipeline_state
    from titan.memory import _gather_daily_metrics

    pipeline = await _assess_pipeline_state()
    metrics = await _gather_daily_metrics()

    # Agent decisions from last 24h
    decisions = await fetch_all(
        """SELECT agent, decision_type, reasoning, created_at
           FROM agent_decisions
           WHERE created_at > NOW() - INTERVAL '24 hours'
           ORDER BY created_at DESC LIMIT 50"""
    )

    # Pipeline errors from last 24h
    errors = await fetch_all(
        """SELECT payload FROM events
           WHERE event_type IN ('pipeline_error', 'pipeline_stage_error')
           AND created_at > NOW() - INTERVAL '24 hours'
           ORDER BY created_at DESC LIMIT 20"""
    )

    # Active rules
    rules = await fetch_all(
        "SELECT id, category, rule_text, metric_before, metric_after, active FROM titan_rules"
    )

    # Source quality
    source_quality = await fetch_all("SELECT * FROM v_source_quality LIMIT 20")

    # Cost per stage
    cost_per_stage = await fetch_all("SELECT * FROM v_cost_per_stage LIMIT 20")

    # Agent self-models
    self_models = await get_all_self_models()

    # Read soul docs
    soul_copy = _read_file("soul/soul_copy.md")
    soul_agent = _read_file("soul/soul_agent.md")

    return {
        "date": date.today().isoformat(),
        "pipeline_state": pipeline,
        "daily_metrics": metrics,
        "recent_decisions": [dict(d) for d in decisions] if decisions else [],
        "recent_errors": [d.get("payload", {}) for d in errors] if errors else [],
        "active_rules": [dict(r) for r in rules] if rules else [],
        "source_quality": [dict(s) for s in source_quality] if source_quality else [],
        "cost_per_stage": [dict(c) for c in cost_per_stage] if cost_per_stage else [],
        "agent_self_models": self_models,
        "soul_copy_content": soul_copy[:3000],
        "soul_agent_content": soul_agent[:2000],
    }


# ── Phase B: Contrarian Debate ────────────────────────────────────

async def _run_alpha(snapshot: dict) -> list[dict]:
    """Alpha observes the system and proposes changes."""
    prompt = f"""You are Alpha, the system optimizer for Perseus — an autonomous AI revenue system.
You've observed the full system for the last 24 hours. Your job: find what's broken,
what's underperforming, and propose specific, data-backed changes.

FULL SYSTEM SNAPSHOT:
{json.dumps(snapshot, indent=2, default=str)[:12000]}

Propose 3-5 specific changes. For each, be precise about:
1. WHAT to change (exact text, value, or rule)
2. WHERE (file path, config key, or rule ID)
3. WHY (cite specific numbers from the snapshot)
4. EXPECTED IMPACT (quantify if possible)
5. CATEGORY: "soul_doc_edit" | "config_change" | "rule_change" | "targeting" | "pricing"

CONSTRAINTS:
- Never touch compliance rules (soul_copy.md lines 34-42)
- Pricing changes max ±$50, range $149-$499
- Max 5 proposals
- Only propose changes with data backing — no vibes

Return JSON:
{{"proposals": [
  {{"what": "...", "where": "...", "why": "...", "expected_impact": "...",
    "category": "...", "confidence": 0.0-1.0,
    "old_value": "...", "new_value": "..."}}
]}}"""

    result = await llm.generate(prompt, model="genius", temperature=0.3, max_tokens=3000,
                                 pipeline_stage="sleep_cycle_alpha")

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
        proposals = data.get("proposals", [])[:5]
        logger.info(f"Alpha proposed {len(proposals)} changes")
        return proposals
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse Alpha proposals")
        return []


async def _run_beta(proposals: list[dict], snapshot: dict) -> list[dict]:
    """Beta challenges every proposal — the devil's advocate."""
    if not proposals:
        return []

    prompt = f"""You are Beta, the devil's advocate for Perseus. Alpha proposed these changes:

PROPOSALS:
{json.dumps(proposals, indent=2, default=str)}

FULL SYSTEM SNAPSHOT:
{json.dumps(snapshot, indent=2, default=str)[:8000]}

For EACH proposal, argue the OTHER side:
- Why could this change HURT the system?
- Is Alpha over-indexing on one day's data?
- Does the data actually support the conclusion?
- Is there a simpler change that achieves the same result?

Then render a FINAL VERDICT for each:
- APPROVE: data is solid, change is safe, expected impact is worth the risk
- MODIFY: the direction is right but the specific change needs adjustment
- REJECT: the data doesn't support it, the risk is too high, or it's premature

Return JSON:
{{"verdicts": [
  {{"proposal_index": 0, "verdict": "approve|modify|reject",
    "counterargument": "...", "modified_proposal": null or {{...}}}}
]}}"""

    result = await llm.generate(prompt, model="genius", temperature=0.4, max_tokens=2500,
                                 pipeline_stage="sleep_cycle_beta")

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
        verdicts = data.get("verdicts", [])
        approved = sum(1 for v in verdicts if v.get("verdict") == "approve")
        modified = sum(1 for v in verdicts if v.get("verdict") == "modify")
        rejected = sum(1 for v in verdicts if v.get("verdict") == "reject")
        logger.info(f"Beta verdicts: {approved} approved, {modified} modified, {rejected} rejected")
        return verdicts
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse Beta verdicts")
        return []


def _filter_surviving(proposals: list[dict], verdicts: list[dict]) -> list[dict]:
    """Return proposals that survived Beta review (approved or modified)."""
    surviving = []
    for verdict in verdicts:
        idx = verdict.get("proposal_index", -1)
        if idx < 0 or idx >= len(proposals):
            continue

        v = verdict.get("verdict", "reject")
        if v == "approve":
            surviving.append(proposals[idx])
        elif v == "modify" and verdict.get("modified_proposal"):
            modified = {**proposals[idx], **verdict["modified_proposal"]}
            surviving.append(modified)
        # reject = skip

    return surviving


# ── Phase C: Apply Changes ────────────────────────────────────────

async def _apply_changes(proposals: list[dict], cycle_id: int) -> int:
    """Apply surviving proposals via the backprop engine."""
    from perseus.backprop import apply_soul_doc_edit, apply_config_change, apply_rule_change

    applied = 0
    for proposal in proposals:
        category = proposal.get("category", "")
        try:
            if category == "soul_doc_edit":
                success = await apply_soul_doc_edit(
                    file_path=proposal.get("where", ""),
                    old_text=proposal.get("old_value", ""),
                    new_text=proposal.get("new_value", ""),
                    reason=proposal.get("why", ""),
                    cycle_id=cycle_id,
                )
                if success:
                    applied += 1

            elif category in ("config_change", "pricing", "targeting"):
                success = await apply_config_change(
                    key=proposal.get("where", ""),
                    new_value=proposal.get("new_value"),
                    reason=proposal.get("why", ""),
                    cycle_id=cycle_id,
                )
                if success:
                    applied += 1

            elif category == "rule_change":
                rule_id = proposal.get("where")
                if rule_id:
                    active = proposal.get("new_value", True)
                    success = await apply_rule_change(
                        rule_id=int(rule_id),
                        active=bool(active),
                        reason=proposal.get("why", ""),
                        cycle_id=cycle_id,
                    )
                    if success:
                        applied += 1

        except Exception as e:
            logger.warning(f"Failed to apply proposal: {proposal.get('what', 'unknown')}: {e}")

    return applied


# ── Self-Model Updates ────────────────────────────────────────────

async def _update_all_self_models(snapshot: dict) -> None:
    """Compute and store self-models for all agents."""
    for agent_name in ["perseus", "titan", "hermes", "clawdbot"]:
        try:
            metrics = await compute_agent_metrics(agent_name)
            await update_self_model(agent_name, metrics)
        except Exception as e:
            logger.debug(f"Self-model update failed for {agent_name}: {e}")


# ── Cell Division Check ──────────────────────────────────────────

async def _check_cell_division(snapshot: dict, cycle_id: int) -> None:
    """Check if the system needs a new specialized agent."""
    from perseus.cell_division import evaluate_division_need
    try:
        await evaluate_division_need(snapshot, cycle_id)
    except Exception as e:
        logger.debug(f"Cell division check failed (non-critical): {e}")


# ── Logging ───────────────────────────────────────────────────────

async def _log_cycle(
    snapshot: dict,
    proposals: list[dict],
    verdicts: list[dict],
    surviving: list[dict],
) -> int:
    """Log the sleep cycle to the database. Returns cycle ID."""
    from psycopg.types.json import Jsonb

    row = await fetch_one(
        """INSERT INTO sleep_cycle_log
           (cycle_date, system_snapshot, alpha_proposals, beta_verdicts, applied_changes)
           VALUES (CURRENT_DATE, %s, %s, %s, %s)
           ON CONFLICT (cycle_date) DO UPDATE SET
               system_snapshot = EXCLUDED.system_snapshot,
               alpha_proposals = EXCLUDED.alpha_proposals,
               beta_verdicts = EXCLUDED.beta_verdicts,
               applied_changes = EXCLUDED.applied_changes
           RETURNING id""",
        (
            Jsonb(snapshot),
            Jsonb({"proposals": proposals}),
            Jsonb({"verdicts": verdicts}),
            Jsonb(surviving),
        ),
    )
    return row["id"] if row else 0


# ── Helpers ───────────────────────────────────────────────────────

def _read_file(relative_path: str) -> str:
    """Read a file from the repo root."""
    full = config.root_dir / relative_path
    if full.exists():
        return full.read_text()
    return ""
