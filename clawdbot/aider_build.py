"""Phase 3 Wave 3: Clawdbot Aider site-builder path.

Wraps shared.aider.clawdbot_loop.run_clawdbot_aider_loop as an opt-in
runtime path for Clawdbot's site generation workflow. Invoked from
clawdbot.site_builder when ENABLE_AIDER_LOOPS=true.

Uses the Architect -> Editor -> Verifier loop:
- Architect: Trinity-Large-Thinking (GENIUS tier) -- plans component tree, copy, CTAs
- Editor: Trinity Mini (LOCAL tier) -- writes HTML/CSS/JS per section
- Verifier: Qwen3-VL-7B (visual) + pytest for JS + anti_slop for copy

Fall back to existing competitive 5-agent build on failure/escalation.

Note on adapter shape: the canonical loop in shared.aider.clawdbot_loop takes a
``lead_profile`` dict and exposes ``max_iterations_per_section``. This handler
accepts a flexible ``site_spec`` dict (brief / brand / sections) so callers
need not know the loop's internal lead-profile shape. We translate to a
lead_profile here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from shared.aider.clawdbot_loop import (
    ClawdbotAiderResult,
    run_clawdbot_aider_loop,
)
from shared.tiers import TierName

logger = logging.getLogger("perseus.clawdbot.aider")


def aider_build_enabled() -> bool:
    """Runtime check -- only route through Aider when operator opts in."""
    return os.environ.get("ENABLE_AIDER_LOOPS", "false").lower() == "true"


def _site_spec_to_lead_profile(site_spec: dict) -> dict:
    """Translate caller-friendly site_spec into the loop's lead_profile shape."""
    brand = site_spec.get("brand") or {}
    return {
        "brief": site_spec.get("brief", ""),
        "business_name": brand.get("name") or site_spec.get("business_name", ""),
        "industry": brand.get("industry") or site_spec.get("industry", ""),
        "voice": brand.get("voice") or site_spec.get("voice", ""),
        "palette": brand.get("palette") or {},
        "sections_requested": site_spec.get("sections", []),
        # Pass through any additional caller fields untouched
        **{k: v for k, v in site_spec.items() if k not in {"brief", "brand", "sections"}},
    }


async def handle_aider_site_build(
    site_spec: dict,
    *,
    llm_client: Any,
    workspace: Path,
) -> dict:
    """Route a site-build request through the Aider loop.

    Returns a dict shaped for clawdbot.site_builder consumption.

    Status values:
      - ``skipped``: feature flag OFF -- caller should run competitive build
      - ``failed``: pre-flight validation failed (missing brief, etc.)
      - ``escalated``: loop ran but visual scores below pass threshold
      - ``complete``: loop succeeded
    """
    if not aider_build_enabled():
        return {"status": "skipped", "reason": "ENABLE_AIDER_LOOPS=false"}

    if not isinstance(site_spec, dict):
        return {"status": "failed", "error": "site_spec must be a dict"}

    brief = site_spec.get("brief", "")
    if not brief:
        return {"status": "failed", "error": "Missing brief in site_spec"}

    # ``workspace`` is reserved for future use (caching rendered sections,
    # writing intermediate artifacts). The canonical loop currently does its
    # own in-memory assembly, so we just log it.
    logger.debug("Aider site build invoked (workspace=%s)", workspace)

    lead_profile = _site_spec_to_lead_profile(site_spec)

    try:
        result: ClawdbotAiderResult = await run_clawdbot_aider_loop(
            lead_profile,
            llm_client=llm_client,
            architect_tier=TierName.GENIUS,  # Trinity-Large-Thinking
            editor_tier=TierName.LOCAL,      # Trinity Mini
            max_iterations_per_section=3,
        )
    except Exception as exc:  # noqa: BLE001 -- defensive boundary
        logger.exception("Clawdbot Aider loop crashed: %s", exc)
        return {"status": "failed", "error": f"Aider exception: {exc}"}

    architect_model = result.page_plan.architect_model if result.page_plan else ""
    editor_model = ""
    visual_score = 0.0
    if result.sections:
        editor_model = result.sections[0].editor_model
        visual_score = sum(s.visual_score for s in result.sections) / len(result.sections)

    return {
        "status": "complete" if result.success else "escalated",
        "html": result.final_html or "",
        "iterations": sum(s.iterations for s in result.sections),
        "architect_model": architect_model,
        "editor_model": editor_model,
        "visual_score": visual_score,
        "error": result.error or None,
    }
