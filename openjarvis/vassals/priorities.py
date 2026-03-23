"""Deterministic priority logic — ported from Perseus.

No LLM call needed. Pure pipeline math.
Rule: always prioritize revenue-closest work first.
Money on the table > leads in the pipe > new discovery.
"""

from __future__ import annotations

from typing import Any, Dict, List


def decide_priorities(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decide which pipeline tasks to schedule based on current state.

    Parameters
    ----------
    state:
        Pipeline state dict from Titan's ``pipeline_status`` capability.
        Expected keys: hot_leads, ready_to_close, ready_to_deliver,
        ready_to_invoice, outreach_active, top_of_funnel, stage_counts,
        task_backlog, recent_errors, pending_reviews.

    Returns
    -------
    dict with keys:
        schedule: list of task names to run NOW
        skip: list of task names to skip this cycle
        reasoning: human-readable explanation
    """
    schedule_now: List[str] = []
    skip_now: List[str] = []
    reasons: List[str] = []

    hot = state.get("hot_leads", 0)
    ready_close = state.get("ready_to_close", 0)
    ready_deliver = state.get("ready_to_deliver", 0)
    ready_invoice = state.get("ready_to_invoice", 0)
    outreach = state.get("outreach_active", 0)
    errors = state.get("recent_errors", 0)
    pending_reviews = state.get("pending_reviews", 0)

    # Priority 0: If errors are spiking, pause and investigate
    if errors > 10:
        reasons.append(f"{errors} errors in last hour — throttling pipeline")
        # Only run health/budget, skip everything else
        return {
            "schedule": ["health_check", "budget_check"],
            "skip": [
                "lead_discovery", "lead_research", "email_compose", "email_send",
                "follow_up_check", "close_interested", "build_sites", "process_invoices",
            ],
            "reasoning": "; ".join(reasons),
            "alert": True,
        }

    # Priority 1: Invoices waiting → money sitting on the table
    if ready_invoice > 0:
        schedule_now.append("process_invoices")
        reasons.append(f"{ready_invoice} ready to invoice")

    # Priority 2: Sites to build → unblock invoicing
    if ready_deliver > 0:
        schedule_now.extend(["build_sites", "site_verify"])
        reasons.append(f"{ready_deliver} sites to build/deploy")

    # Priority 3: Hot leads → close them before they cool off
    if hot > 0 or ready_close > 0:
        schedule_now.extend(["close_interested", "follow_up_check"])
        reasons.append(f"{hot} hot leads, {ready_close} ready to close")

    # Priority 4: Active outreach needs analytics + follow-ups
    if outreach > 0:
        schedule_now.extend(["sync_analytics", "email_send", "email_compose", "follow_up_check"])
        reasons.append(f"{outreach} in active outreach")

    # Priority 5: Review queue — operator has items to approve
    if pending_reviews > 0:
        reasons.append(f"{pending_reviews} pending operator reviews")

    # Priority 6: Top of funnel — only if we're not overwhelmed downstream
    if hot <= 3 and ready_deliver <= 2:
        schedule_now.extend(["lead_discovery", "lead_research", "enrich_leads"])
    else:
        skip_now.extend(["lead_discovery", "lead_research", "enrich_leads"])
        reasons.append(f"Skipping discovery: {hot} hot + {ready_deliver} to deliver — focus downstream")

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for name in schedule_now:
        if name not in seen:
            seen.add(name)
            deduped.append(name)

    # Everything not scheduled is implicitly skipped
    ALL_SKIPPABLE = {
        "lead_discovery", "lead_research", "enrich_leads",
        "email_compose", "email_send", "follow_up_check",
        "close_interested", "build_sites", "site_verify",
        "process_invoices", "sync_analytics",
    }
    implicit_skip = ALL_SKIPPABLE - seen
    skip_now.extend(s for s in implicit_skip if s not in skip_now)

    reasoning = "; ".join(reasons) if reasons else "Normal pipeline flow"

    return {
        "schedule": deduped,
        "skip": skip_now,
        "reasoning": reasoning,
    }


__all__ = ["decide_priorities"]
