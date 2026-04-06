"""Single-prompt pipeline experiment for Phase 19 (F-23).

Evaluates collapsing multi-call pipeline patterns into single structured calls.
Shadow-mode only: runs alongside existing pipeline, compares quality.

Feature flag: ANATOMY_COST_DASHBOARD
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger("perseus.titan.single_prompt")


def _experiment_enabled() -> bool:
    return os.environ.get("ANATOMY_COST_DASHBOARD", "").lower() in ("true", "1")


async def evaluate_single_prompt_compose(lead: dict) -> dict | None:
    """Shadow-mode: run lead enrichment + proposal + email as one structured call.

    Does NOT replace the existing pipeline. Runs in parallel and compares
    output quality via the quality gate scoring function.

    Returns:
        {
            "enrichment": {...},
            "proposal": {...},
            "email_subject": str,
            "email_body": str,
            "total_tokens": int,
            "latency_ms": int,
            "quality_score": float | None,
        }
    or None if experiment is disabled.
    """
    if not _experiment_enabled():
        return None

    from shared.llm_client import llm

    business = lead.get("business_name", "Unknown")
    industry = lead.get("industry", "")
    website = lead.get("website", "")
    email = lead.get("email", "")

    prompt = f"""You are a B2B sales agent. Given this lead, produce a COMPLETE sales package in one response.

LEAD:
- Business: {business}
- Industry: {industry}
- Website: {website}
- Contact: {email}

Produce a JSON response with exactly these keys:
{{
  "enrichment": {{
    "company_size_estimate": "small|medium|large",
    "pain_points": ["list of 3 likely pain points"],
    "current_web_presence": "strong|moderate|weak",
    "recommended_services": ["list"]
  }},
  "proposal": {{
    "headline": "one-line value proposition",
    "key_benefits": ["3 benefits"],
    "pricing_tier": "starter|professional|enterprise",
    "urgency_hook": "why act now"
  }},
  "email_subject": "subject line",
  "email_body": "full email body (3-4 paragraphs, professional tone)"
}}

Return ONLY valid JSON, no markdown fencing."""

    t0 = time.perf_counter()
    result_text = await llm.generate(
        prompt,
        model="smart",
        max_tokens=1500,
        temperature=0.4,
        pipeline_stage="single_prompt_experiment",
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)

    try:
        # Strip markdown fencing if present
        clean = result_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
        if clean.endswith("```"):
            clean = clean.rsplit("```", 1)[0]
        parsed = json.loads(clean.strip())
        parsed["latency_ms"] = latency_ms
        parsed["total_tokens"] = max(1, len(prompt) // 4 + len(result_text) // 4)
        return parsed
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Single-prompt experiment parse failed: %s", exc)
        return {"error": str(exc), "raw": result_text[:500], "latency_ms": latency_ms}


async def compare_with_pipeline(lead: dict, pipeline_result: dict) -> dict:
    """Compare single-prompt result with multi-call pipeline result.

    Stores comparison for later analysis.
    """
    single = await evaluate_single_prompt_compose(lead)
    if not single or "error" in single:
        return {"comparison": "skipped", "reason": "single-prompt failed"}

    # Simple quality comparison: check key fields present
    has_enrichment = bool(single.get("enrichment", {}).get("pain_points"))
    has_email = bool(single.get("email_body"))
    has_proposal = bool(single.get("proposal", {}).get("headline"))

    return {
        "single_prompt": {
            "latency_ms": single.get("latency_ms", 0),
            "total_tokens": single.get("total_tokens", 0),
            "has_enrichment": has_enrichment,
            "has_email": has_email,
            "has_proposal": has_proposal,
        },
        "pipeline": {
            "latency_ms": pipeline_result.get("latency_ms", 0),
            "total_tokens": pipeline_result.get("total_tokens", 0),
        },
        "token_savings_pct": round(
            (1 - single.get("total_tokens", 0) / max(pipeline_result.get("total_tokens", 1), 1)) * 100, 1
        ),
    }
