"""
Revenue-driven expansion gate for Perseus.

Perseus should only expand itself when the expansion is tied to money:
- more revenue
- better close rate
- faster throughput
- lower operating cost

This module reviews real pipeline bottlenecks, asks the LLM for the smallest
capability that could fix them, and only advances opportunities that clear a
strict ROI gate. Discovery skills are the first concrete shadow-rollout path.
"""

import json
import logging
import os
from decimal import Decimal

try:
    from psycopg.types.json import Jsonb
except ImportError:
    Jsonb = None  # type: ignore[assignment,misc]

from shared.comms import request_task_result
from shared.db import emit_event, execute, fetch_one, fetch_val, get_config, set_config
from shared.llm_client import llm
from shared.skill_loader import find_skill, list_installed_skills
from titan.memory import get_relevant_learnings

logger = logging.getLogger("perseus.titan.expansion")


def _as_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _is_bandit_expansion_enabled() -> bool:
    """Check if adaptive bandit thresholds are enabled via feature flag."""
    return os.environ.get("ENABLE_BANDIT_EXPANSION", "").lower() in ("1", "true")


def _detect_revenue_bottlenecks(metrics: dict) -> list[dict]:
    """Translate raw metrics into money-linked bottlenecks worth solving.

    When ENABLE_BANDIT_EXPANSION is true, thresholds are sampled from Thompson
    sampling bandits (adaptive). Otherwise, hardcoded values are used.
    """
    if _is_bandit_expansion_enabled():
        from titan.adaptive_thresholds import AdaptiveThresholds

        at = AdaptiveThresholds()
        # Adaptive thresholds: sample from bandit posteriors (sync/in-memory).
        # Scale bandit output [0,1] to the metric's natural range.
        reply_threshold = at.get_threshold("reply_rate_threshold") * 5.0  # ~1.5 default
        interest_threshold = at.get_threshold("interest_rate_threshold") * 25.0  # ~12.0 default
        proposal_threshold = at.get_threshold("proposal_backlog_threshold") * 5.0  # ~3.0 default
        uninvoiced_threshold = at.get_threshold("uninvoiced_threshold") * 5.0  # ~2.0 default
        missing_email_threshold = at.get_threshold("missing_email_threshold") * 15.0  # ~10.0 default
        logger.info(
            "Bandit thresholds: reply=%.2f interest=%.2f proposal=%.2f uninvoiced=%.2f missing_email=%.2f",
            reply_threshold, interest_threshold, proposal_threshold,
            uninvoiced_threshold, missing_email_threshold,
        )
    else:
        # Original hardcoded values
        reply_threshold = 1.5
        interest_threshold = 12.0
        proposal_threshold = 3
        uninvoiced_threshold = 2
        missing_email_threshold = 10

    bottlenecks: list[dict] = []

    if metrics.get("emails_sent_14d", 0) >= 100 and metrics.get("reply_rate_14d", 0.0) < reply_threshold:
        bottlenecks.append({
            "stage": "lead_discovery",
            "bottleneck": "low_reply_rate",
            "metric": "reply_rate_14d",
            "current_value": metrics.get("reply_rate_14d", 0.0),
            "target_value": 2.5,
            "impact": "Low reply rate limits revenue because more send volume is wasted.",
        })

    if metrics.get("replies_14d", 0) >= 15 and metrics.get("interest_rate_14d", 0.0) < interest_threshold:
        bottlenecks.append({
            "stage": "email_compose",
            "bottleneck": "low_interest_rate",
            "metric": "interest_rate_14d",
            "current_value": metrics.get("interest_rate_14d", 0.0),
            "target_value": 18.0,
            "impact": "Replies are happening, but too few become sales conversations.",
        })

    if metrics.get("interested_open", 0) >= 5 and metrics.get("proposal_backlog", 0) >= proposal_threshold:
        bottlenecks.append({
            "stage": "close_deal",
            "bottleneck": "proposal_backlog",
            "metric": "proposal_backlog",
            "current_value": metrics.get("proposal_backlog", 0),
            "target_value": 1,
            "impact": "Interested leads are waiting too long to see demos and proposals.",
        })

    if metrics.get("closed_uninvoiced", 0) >= uninvoiced_threshold:
        bottlenecks.append({
            "stage": "invoice",
            "bottleneck": "invoice_delay",
            "metric": "closed_uninvoiced",
            "current_value": metrics.get("closed_uninvoiced", 0),
            "target_value": 0,
            "impact": "Revenue is delayed because closed deals are waiting for invoice/payment routing.",
        })

    if metrics.get("discovered_missing_email_7d", 0) >= missing_email_threshold:
        bottlenecks.append({
            "stage": "lead_discovery",
            "bottleneck": "missing_contact_data",
            "metric": "discovered_missing_email_7d",
            "current_value": metrics.get("discovered_missing_email_7d", 0),
            "target_value": 3,
            "impact": "Lead throughput is being wasted on records that cannot be reached quickly.",
        })

    return bottlenecks


def _gate_candidate(candidate: dict, *, budget: Decimal, min_roi: float) -> tuple[bool, str]:
    """Revenue-first gate: only keep cheap, positive-ROI expansion ideas."""
    revenue_gain = _as_decimal(candidate.get("expected_monthly_revenue_gain", 0))
    monthly_cost = _as_decimal(candidate.get("expected_monthly_cost", 0))
    expected_roi = float(candidate.get("expected_roi", 0) or 0)

    if revenue_gain <= 0:
        return False, "Projected revenue gain must be positive"
    if monthly_cost < 0:
        return False, "Projected cost cannot be negative"
    if monthly_cost > budget:
        return False, f"Projected monthly cost ${monthly_cost} exceeds expansion budget ${budget}"
    if revenue_gain <= monthly_cost:
        return False, "Projected revenue gain must exceed projected monthly cost"
    if expected_roi < min_roi:
        return False, f"Expected ROI {expected_roi:.2f} is below required {min_roi:.2f}"
    if candidate.get("capability_type") not in {"skill", "prompt", "tool", "agent"}:
        return False, "Unsupported capability_type"
    return True, ""


def _pick_builder_skill(installed_skill_names: list[str]) -> str:
    """Pick the best installed authoring skill for code/tool generation via ClawdBot."""
    names = [name for name in installed_skill_names if name]
    exact_priority = [
        "codex-collab",
        "claude-code",
        "openclaw-coder",
        "mcp-builder",
        "tool-builder",
    ]
    lowered = {name.lower(): name for name in names}
    for preferred in exact_priority:
        if preferred in lowered:
            return lowered[preferred]

    for name in names:
        lowered_name = name.lower()
        if "codex" in lowered_name or "claude" in lowered_name:
            return name
    for name in names:
        lowered_name = name.lower()
        if "builder" in lowered_name or "coder" in lowered_name or "mcp" in lowered_name:
            return name
    return ""


async def review_revenue_expansion() -> list[int]:
    """Find ROI-positive expansion opportunities and start the smallest safe rollout."""
    if not await get_config("expansion_enabled", True):
        logger.info("Revenue expansion review is disabled")
        return []

    await _evaluate_active_shadow_discovery()

    metrics = await _collect_expansion_metrics()
    bottlenecks = _detect_revenue_bottlenecks(metrics)
    if not bottlenecks:
        logger.info("No revenue bottlenecks currently justify expansion")
        return []

    installed_skills = [skill["name"] for skill in list_installed_skills()]
    builder_skill = _pick_builder_skill(installed_skills)
    learnings = await get_relevant_learnings(
        "revenue bottlenecks, throughput constraints, discovery quality, close rate, invoicing friction"
    )

    monthly_budget = _as_decimal(await get_config("expansion_monthly_budget", 50))
    min_roi = float(await get_config("expansion_min_expected_roi", 1.5) or 1.5)
    shadow_percent = int(await get_config("expansion_shadow_percent", 10) or 10)
    active_shadow_skill = str(await get_config("active_shadow_discovery_skill", "") or "")

    prompt = f"""You are Titan's expansion gate.

Project goal: make more money, not build more machinery.
Only propose the smallest capability that is likely to increase revenue, improve margin,
or reduce time-to-close.

REAL METRICS:
{json.dumps(metrics, indent=2, default=str)}

BOTTLENECKS:
{json.dumps(bottlenecks, indent=2, default=str)}

INSTALLED SKILLS:
{json.dumps(installed_skills)}

LEARNINGS:
{learnings}

RULES:
1. Return at most 3 opportunities.
2. Prefer existing installed skills over new code.
3. Only propose tool/agent builds when no installed skill plausibly solves the bottleneck.
4. Expected monthly revenue gain must exceed expected monthly cost.
5. If a discovery skill already exists, set stage=lead_discovery and capability_type=skill.
6. Keep the smallest_step concrete and shadow-testable.

Return JSON list:
[{{"title":"...",
   "stage":"lead_discovery|email_compose|close_deal|invoice",
   "bottleneck":"...",
   "capability_type":"skill|prompt|tool|agent",
   "capability_name":"...",
   "target_metric":"...",
   "success_metric":"...",
   "expected_monthly_revenue_gain": 1200,
   "expected_monthly_cost": 20,
   "expected_roi": 60.0,
   "reasoning":"...",
   "smallest_step":"...",
   "rollback_condition":"..."}}]"""

    result = await llm.generate(prompt, model="smart", temperature=0.3, use_dna=True, daemon_name="titan")
    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        candidates = json.loads(result[start:end])
    except Exception as e:
        logger.warning(f"Could not parse revenue expansion review: {e}")
        return []

    if not isinstance(candidates, list):
        return []

    created_ids: list[int] = []
    for raw_candidate in candidates[:3]:
        candidate = dict(raw_candidate or {})
        allowed, reason = _gate_candidate(candidate, budget=monthly_budget, min_roi=min_roi)
        if not allowed:
            logger.info("Rejected expansion candidate '%s': %s", candidate.get("title", "untitled"), reason)
            continue

        existing = await fetch_one(
            """SELECT id FROM revenue_expansion_opportunities
               WHERE capability_name = %s
                 AND stage = %s
                 AND status IN ('proposed', 'shadow', 'adopted')
               ORDER BY created_at DESC LIMIT 1""",
            (candidate.get("capability_name", ""), candidate.get("stage", "")),
        )
        if existing:
            continue

        status = "proposed"
        capability_name = str(candidate.get("capability_name", "")).strip()
        if (
            candidate.get("stage") == "lead_discovery"
            and candidate.get("capability_type") == "skill"
            and capability_name
            and find_skill(capability_name)
            and not active_shadow_skill
        ):
            status = "shadow"

        row = await fetch_one(
            """INSERT INTO revenue_expansion_opportunities (
                   title, stage, bottleneck, capability_type, capability_name,
                   target_metric, success_metric, expected_monthly_revenue_gain,
                   expected_monthly_cost, expected_roi, status, shadow_percent,
                   reasoning, smallest_step, rollback_condition, proposal, shadow_started_at
               )
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                candidate.get("title", capability_name or "Expansion opportunity"),
                candidate.get("stage", ""),
                candidate.get("bottleneck", ""),
                candidate.get("capability_type", ""),
                capability_name,
                candidate.get("target_metric", ""),
                candidate.get("success_metric", ""),
                candidate.get("expected_monthly_revenue_gain", 0),
                candidate.get("expected_monthly_cost", 0),
                candidate.get("expected_roi", 0),
                status,
                shadow_percent,
                candidate.get("reasoning", ""),
                candidate.get("smallest_step", ""),
                candidate.get("rollback_condition", ""),
                _jsonb(candidate),
                None,
            ),
        )

        if not row:
            continue

        opportunity_id = row["id"]
        created_ids.append(opportunity_id)
        await emit_event(
            "revenue_expansion_opportunity",
            {
                "opportunity_id": opportunity_id,
                "title": candidate.get("title", ""),
                "stage": candidate.get("stage", ""),
                "capability_type": candidate.get("capability_type", ""),
                "capability_name": capability_name,
                "status": status,
                "expected_roi": candidate.get("expected_roi", 0),
                "expected_monthly_revenue_gain": candidate.get("expected_monthly_revenue_gain", 0),
                "expected_monthly_cost": candidate.get("expected_monthly_cost", 0),
            },
        )

        if status == "shadow":
            await execute(
                "UPDATE revenue_expansion_opportunities SET shadow_started_at = NOW() WHERE id = %s",
                (opportunity_id,),
            )
            await set_config("active_shadow_discovery_skill", capability_name)
            await set_config("active_shadow_opportunity_id", opportunity_id)
            active_shadow_skill = capability_name
            await emit_event(
                "revenue_expansion_shadow_started",
                {
                    "opportunity_id": opportunity_id,
                    "skill": capability_name,
                    "shadow_percent": shadow_percent,
                },
            )
        elif candidate.get("capability_type") in {"tool", "agent"}:
            await _request_builder_blueprint(
                opportunity_id=opportunity_id,
                candidate=candidate,
                builder_skill=builder_skill,
            )

    return created_ids


async def _collect_expansion_metrics() -> dict:
    """Collect the metrics needed to decide whether expansion is revenue-positive."""
    row = await fetch_one(
        """SELECT
               COALESCE(SUM(emails_sent), 0) AS emails_sent_14d,
               COALESCE(SUM(replies), 0) AS replies_14d,
               COALESCE(SUM(interested_replies), 0) AS interested_replies_14d
           FROM outreach_metrics
           WHERE date >= CURRENT_DATE - INTERVAL '14 days'"""
    ) or {}

    emails_sent = int(row.get("emails_sent_14d", 0) or 0)
    replies = int(row.get("replies_14d", 0) or 0)
    interested_replies = int(row.get("interested_replies_14d", 0) or 0)

    interested_open = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating')"
    ) or 0
    proposal_backlog = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status IN ('interested', 'demo_built')"
    ) or 0
    closed_uninvoiced = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'closed'"
    ) or 0
    discovered_missing_email = await fetch_val(
        """SELECT COUNT(*) FROM clients
           WHERE status = 'discovered'
             AND email = ''
             AND created_at >= NOW() - INTERVAL '7 days'"""
    ) or 0
    paid_revenue_30d = await fetch_val(
        """SELECT COALESCE(SUM(amount), 0) FROM deals
           WHERE status = 'paid' AND paid_at >= NOW() - INTERVAL '30 days'"""
    ) or 0

    return {
        "emails_sent_14d": emails_sent,
        "replies_14d": replies,
        "interested_replies_14d": interested_replies,
        "reply_rate_14d": round(replies / max(emails_sent, 1) * 100, 2),
        "interest_rate_14d": round(interested_replies / max(replies, 1) * 100, 2),
        "interested_open": int(interested_open),
        "proposal_backlog": int(proposal_backlog),
        "closed_uninvoiced": int(closed_uninvoiced),
        "discovered_missing_email_7d": int(discovered_missing_email),
        "paid_revenue_30d": float(paid_revenue_30d),
    }


async def _evaluate_active_shadow_discovery() -> None:
    """Keep discovery shadow rollouts only when they outperform the baseline."""
    opportunity_id = int(await get_config("active_shadow_opportunity_id", 0) or 0)
    if not opportunity_id:
        return

    opportunity = await fetch_one(
        """SELECT id, capability_name, created_at, status
           FROM revenue_expansion_opportunities
           WHERE id = %s AND status = 'shadow'""",
        (opportunity_id,),
    )
    if not opportunity:
        await set_config("active_shadow_discovery_skill", "")
        await set_config("active_shadow_opportunity_id", 0)
        return

    min_sample = int(await get_config("expansion_shadow_min_sample", 10) or 10)
    marker = f"expansion:{opportunity_id}"

    shadow = await fetch_one(
        """SELECT
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating', 'closed', 'building', 'deployed', 'invoiced', 'paid')) AS interested,
               COUNT(*) FILTER (WHERE status IN ('closed', 'building', 'deployed', 'invoiced', 'paid')) AS closed
           FROM clients
           WHERE source_campaign = %s""",
        (marker,),
    ) or {}

    total = int(shadow.get("total", 0) or 0)
    if total < min_sample:
        return

    baseline = await fetch_one(
        """SELECT
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating', 'closed', 'building', 'deployed', 'invoiced', 'paid')) AS interested,
               COUNT(*) FILTER (WHERE status IN ('closed', 'building', 'deployed', 'invoiced', 'paid')) AS closed
           FROM clients
           WHERE created_at >= %s
             AND COALESCE(source_campaign, '') <> %s""",
        (opportunity["created_at"], marker),
    ) or {}

    shadow_interested_rate = float(shadow.get("interested", 0) or 0) / max(total, 1)
    shadow_close_rate = float(shadow.get("closed", 0) or 0) / max(total, 1)
    baseline_total = int(baseline.get("total", 0) or 0)
    baseline_interested_rate = float(baseline.get("interested", 0) or 0) / max(baseline_total, 1)
    baseline_close_rate = float(baseline.get("closed", 0) or 0) / max(baseline_total, 1)

    if int(shadow.get("interested", 0) or 0) == 0 and int(shadow.get("closed", 0) or 0) == 0:
        await execute(
            """UPDATE revenue_expansion_opportunities
               SET status = 'rejected',
                   decided_at = NOW(),
                   observed_summary = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (
                _jsonb({
                    "shadow_total": total,
                    "shadow_interested_rate": round(shadow_interested_rate, 4),
                    "shadow_close_rate": round(shadow_close_rate, 4),
                    "baseline_interested_rate": round(baseline_interested_rate, 4),
                    "baseline_close_rate": round(baseline_close_rate, 4),
                }),
                opportunity_id,
            ),
        )
        await set_config("active_shadow_discovery_skill", "")
        await set_config("active_shadow_opportunity_id", 0)
        await emit_event(
            "revenue_expansion_rejected",
            {
                "opportunity_id": opportunity_id,
                "skill": opportunity["capability_name"],
                "shadow_total": total,
            },
        )
        return

    if shadow_close_rate >= baseline_close_rate and shadow_interested_rate >= baseline_interested_rate:
        await execute(
            """UPDATE revenue_expansion_opportunities
               SET status = 'adopted',
                   decided_at = NOW(),
                   observed_summary = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (
                _jsonb({
                    "shadow_total": total,
                    "shadow_interested_rate": round(shadow_interested_rate, 4),
                    "shadow_close_rate": round(shadow_close_rate, 4),
                    "baseline_interested_rate": round(baseline_interested_rate, 4),
                    "baseline_close_rate": round(baseline_close_rate, 4),
                }),
                opportunity_id,
            ),
        )
        await set_config("preferred_discovery_skill", opportunity["capability_name"])
        await set_config("active_shadow_discovery_skill", "")
        await set_config("active_shadow_opportunity_id", 0)
        await emit_event(
            "revenue_expansion_adopted",
            {
                "opportunity_id": opportunity_id,
                "skill": opportunity["capability_name"],
                "shadow_total": total,
            },
        )
        return


def _jsonb(value: dict):
    return Jsonb(value)


async def _request_builder_blueprint(opportunity_id: int, candidate: dict, builder_skill: str) -> None:
    """Ask ClawdBot/OpenClaw to generate the build blueprint for tool/agent expansion."""
    if not builder_skill:
        await emit_event(
            "revenue_expansion_needs_builder",
            {
                "opportunity_id": opportunity_id,
                "title": candidate.get("title", ""),
                "reason": "No Codex/Claude/OpenClaw builder skill is installed for ClawdBot.",
            },
        )
        return

    prompt = f"""You are generating an implementation blueprint for Perseus through ClawdBot.

This expansion already passed the revenue gate.
It MUST be written through the ClawdBot/OpenClaw skill channel, not by Titan directly.

Opportunity:
{json.dumps(candidate, indent=2, default=str)}

Requirements:
1. Keep the implementation as small as possible.
2. Focus only on changes likely to increase revenue, margin, or throughput.
3. Prefer modifying existing files over adding new subsystems.
4. Include rollout, verification, and rollback.

Return JSON:
{{
  "authoring_channel": "clawdbot_openclaw",
  "writer": "codex|claude_code",
  "summary": "...",
  "files": ["path1", "path2"],
  "steps": ["...", "..."],
  "verification": ["...", "..."],
  "rollback": "..."
}}"""

    result = await request_task_result(
        "skill_execute",
        payload={
            "skill_name": builder_skill,
            "prompt": prompt,
            "context": {
                "opportunity_id": str(opportunity_id),
                "expansion_type": str(candidate.get("capability_type", "")),
                "goal": "make more money",
            },
        },
        timeout_seconds=180,
    )

    if not result or not result.get("ok"):
        await emit_event(
            "revenue_expansion_needs_builder",
            {
                "opportunity_id": opportunity_id,
                "title": candidate.get("title", ""),
                "reason": f"Builder skill {builder_skill} did not return a blueprint.",
            },
        )
        return

    blueprint = result.get("result", {}).get("result", "")
    await execute(
        """UPDATE revenue_expansion_opportunities
           SET proposal = %s,
               updated_at = NOW()
           WHERE id = %s""",
        (_jsonb({
            "authoring_channel": "clawdbot_openclaw",
            "builder_skill": builder_skill,
            "blueprint": blueprint,
        }), opportunity_id),
    )
    await emit_event(
        "revenue_expansion_blueprint_ready",
        {
            "opportunity_id": opportunity_id,
            "title": candidate.get("title", ""),
            "builder_skill": builder_skill,
        },
    )
