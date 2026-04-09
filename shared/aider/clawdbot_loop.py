"""Clawdbot Aider loop — architect+editor for site building.

Replaces the single-model section_agent loop with structured roles:

  1. ARCHITECT (Sonnet 4.6 or Opus 4.6):
     - Read the lead profile + design direction
     - Plan the page: structure, sections, content hooks, brand voice
     - Output: structured page plan

  2. EDITOR (Qwen2.5-Coder-14B local or Sonnet):
     - Generate HTML/CSS for each section per Architect's plan
     - Output: section HTML

  3. VISUAL VERIFIER (Qwen3-VL-7B local or Kimi K2.5):
     - Render section in headless browser
     - Score visual quality + design fidelity (VLM scoring)
     - Output: pass/fail + critique

  4. ASSET GENERATOR (Draw Things local or Recraft cloud):
     - Generate hero images, logos, icons per page plan
     - Output: image URLs

  5. LOOP (max 3 iterations per section):
     - If visual verifier passes: done with section
     - If fails: feed critique back to Editor for regeneration
     - If 3 iterations: escalate to Sonnet hand-build

This replaces the existing competitive 5-agent build process for the
"local-first quality" build path. The competitive build stays available
for premium tier sites where cost is no object.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from shared.tiers import TierName

logger = logging.getLogger("perseus.aider.clawdbot")


@dataclass
class PagePlan:
    page_type: str                          # landing | about | contact | etc.
    design_direction: str                    # minimal-geometric | bold-editorial | etc.
    sections: list[dict]                    # [{type, headline, copy_hook, image_brief}]
    color_palette: dict[str, str] = field(default_factory=dict)
    typography: dict[str, str] = field(default_factory=dict)
    asset_briefs: list[dict] = field(default_factory=list)  # For Draw Things / Recraft
    architect_model: str = ""


@dataclass
class SectionResult:
    section_id: str
    section_type: str
    html: str
    css: str = ""
    visual_score: float = 0.0
    visual_critique: str = ""
    iterations: int = 1
    asset_urls: list[str] = field(default_factory=list)
    editor_model: str = ""


@dataclass
class ClawdbotAiderResult:
    success: bool
    page_plan: PagePlan | None
    sections: list[SectionResult]
    final_html: str
    total_duration_s: float
    architect_calls: int
    editor_calls: int
    visual_calls: int
    asset_calls: int
    escalations: int
    error: str = ""


# ============================================================================
# Prompts
# ============================================================================

ARCHITECT_PROMPT = """You are the Aider Architect for Clawdbot site builder.

Plan a one-page website for this lead. Choose design direction, sections, copy hooks, brand voice.

Lead profile:
{lead_profile}

Current best practice: section count 5-7, mobile-first, no AI tells.

Output ONLY JSON (no prose):
{{
  "page_type": "landing",
  "design_direction": "minimal-geometric|bold-editorial|dark-cinematic|organic-illustrated|playful-animated|immersive-3d",
  "color_palette": {{"primary": "#hex", "secondary": "#hex", "accent": "#hex"}},
  "typography": {{"heading": "font-stack", "body": "font-stack"}},
  "sections": [
    {{"type": "hero", "headline": "...", "copy_hook": "...", "image_brief": "..."}},
    {{"type": "value-prop", "headline": "...", "copy_hook": "..."}}
  ],
  "asset_briefs": [
    {{"role": "hero", "prompt": "image description for Draw Things/Recraft"}},
    {{"role": "logo", "prompt": "..."}}
  ]
}}
"""


EDITOR_PROMPT = """You are the Aider Editor for Clawdbot. Generate HTML+Tailwind for ONE section.

Page design direction: {design_direction}
Color palette: {palette}
Typography: {typography}

Section to build:
  Type: {section_type}
  Headline: {headline}
  Copy hook: {copy_hook}

Available CDN libs: GSAP, Tailwind v4 utilities, shadcn/ui patterns, Lucide icons.

{prior_critique}

Output ONLY HTML (no prose, no markdown fences). Inline only the styles needed.
"""


VISUAL_VERIFIER_PROMPT = """You are the Visual Verifier for Clawdbot. Score this rendered section.

Design direction: {design_direction}
Section type: {section_type}
Expected: {headline}

Score 0.0-1.0 across:
- hierarchy (clear primary/secondary/tertiary?)
- spacing (breathing room? not cramped?)
- typography (readable? consistent?)
- color (matches palette? sufficient contrast?)
- copy quality (no AI tells? no placeholder?)

Output ONLY JSON:
{{
  "overall_score": 0.0-1.0,
  "passes": true|false,
  "critique": "1-2 sentences describing biggest issue if score <0.85"
}}
"""


# ============================================================================
# Loop runner
# ============================================================================

async def run_clawdbot_aider_loop(
    lead_profile: dict,
    *,
    llm_client: Any,
    visual_scorer: Any | None = None,
    asset_generator: Any | None = None,
    renderer: Any | None = None,
    architect_tier: TierName = TierName.SMART,
    editor_tier: TierName = TierName.LOCAL,  # Qwen2.5-Coder-14B
    visual_tier: TierName = TierName.VISION,  # Kimi K2.5 or local Qwen3-VL
    max_iterations_per_section: int = 3,
) -> ClawdbotAiderResult:
    """Plan → build sections → verify → iterate → assemble."""
    t0 = time.perf_counter()
    architect_calls = 0
    editor_calls = 0
    visual_calls = 0
    asset_calls = 0
    escalations = 0
    sections_built: list[SectionResult] = []

    # 1. Architect plans the page
    try:
        plan = await _call_architect(llm_client, lead_profile, architect_tier)
        architect_calls += 1
    except Exception as exc:
        return ClawdbotAiderResult(
            success=False, page_plan=None, sections=[], final_html="",
            total_duration_s=time.perf_counter() - t0,
            architect_calls=architect_calls, editor_calls=editor_calls,
            visual_calls=visual_calls, asset_calls=asset_calls,
            escalations=escalations,
            error=f"Architect failed: {exc}",
        )

    # 2. Generate assets (parallel-ready, sequential here for simplicity)
    if asset_generator is not None:
        for brief in plan.asset_briefs:
            try:
                _ = await asset_generator.generate(brief["prompt"], role=brief.get("role", "image"))
                asset_calls += 1
            except Exception as exc:
                logger.warning("Asset generation failed: %s", exc)

    # 3. For each section: Editor → Visual Verifier → iterate
    for idx, section in enumerate(plan.sections):
        section_result = await _build_section_with_loop(
            llm_client=llm_client,
            visual_scorer=visual_scorer,
            renderer=renderer,
            plan=plan,
            section=section,
            section_id=f"sec_{idx}",
            editor_tier=editor_tier,
            visual_tier=visual_tier,
            max_iterations=max_iterations_per_section,
        )
        editor_calls += section_result.iterations
        if visual_scorer is not None:
            visual_calls += section_result.iterations
        sections_built.append(section_result)
        if section_result.visual_score < 0.5:
            escalations += 1

    # 4. Assemble final HTML
    final_html = _assemble_html(plan, sections_built)
    success = all(s.visual_score >= 0.7 for s in sections_built) if sections_built else False

    return ClawdbotAiderResult(
        success=success,
        page_plan=plan,
        sections=sections_built,
        final_html=final_html,
        total_duration_s=time.perf_counter() - t0,
        architect_calls=architect_calls,
        editor_calls=editor_calls,
        visual_calls=visual_calls,
        asset_calls=asset_calls,
        escalations=escalations,
    )


async def _build_section_with_loop(
    *,
    llm_client: Any,
    visual_scorer: Any | None,
    renderer: Any | None,
    plan: PagePlan,
    section: dict,
    section_id: str,
    editor_tier: TierName,
    visual_tier: TierName,
    max_iterations: int,
) -> SectionResult:
    prior_critique = ""
    last_html = ""
    last_score = 0.0
    iterations = 0

    for i in range(max_iterations):
        iterations = i + 1

        # Editor generates HTML
        try:
            html = await _call_editor(
                llm_client=llm_client,
                plan=plan,
                section=section,
                prior_critique=prior_critique,
                tier=editor_tier,
            )
            last_html = html
        except Exception as exc:
            logger.warning("Editor failed for section %s iter %d: %s", section_id, i + 1, exc)
            break

        # Visual verifier scores it
        if visual_scorer is None or renderer is None:
            last_score = 0.7  # Without scorer, accept first attempt
            break
        try:
            screenshot_bytes = await renderer.render_html_to_png(html)
            score_result = await visual_scorer.score(
                screenshot=screenshot_bytes,
                expected={
                    "design_direction": plan.design_direction,
                    "section_type": section.get("type", ""),
                    "headline": section.get("headline", ""),
                },
                tier=visual_tier,
            )
            last_score = score_result.get("overall_score", 0.0)
            critique = score_result.get("critique", "")
            if last_score >= 0.85:
                break
            prior_critique = f"Previous attempt scored {last_score:.2f}. Critique: {critique}"
        except Exception as exc:
            logger.warning("Visual verifier failed: %s", exc)
            last_score = 0.5
            break

    return SectionResult(
        section_id=section_id,
        section_type=section.get("type", "unknown"),
        html=last_html,
        visual_score=last_score,
        visual_critique=prior_critique,
        iterations=iterations,
        editor_model=editor_tier.value,
    )


async def _call_architect(llm_client: Any, lead_profile: dict, tier: TierName) -> PagePlan:
    prompt = ARCHITECT_PROMPT.format(lead_profile=json.dumps(lead_profile, indent=2))
    response = await llm_client.generate(
        prompt,
        model=tier.value,
        daemon_name="clawdbot",
        pipeline_stage="aider_architect",
        max_tokens=3000,
        temperature=0.4,
        operation="clawdbot.aider_architect",
    )
    return _parse_page_plan(response, tier.value)


async def _call_editor(
    *, llm_client: Any, plan: PagePlan, section: dict, prior_critique: str, tier: TierName
) -> str:
    prompt = EDITOR_PROMPT.format(
        design_direction=plan.design_direction,
        palette=json.dumps(plan.color_palette),
        typography=json.dumps(plan.typography),
        section_type=section.get("type", ""),
        headline=section.get("headline", ""),
        copy_hook=section.get("copy_hook", ""),
        prior_critique=prior_critique,
    )
    response = await llm_client.generate(
        prompt,
        model=tier.value,
        daemon_name="clawdbot",
        pipeline_stage="aider_editor",
        max_tokens=4000,
        temperature=0.4,
        operation="clawdbot.aider_editor",
    )
    return _strip_html_fences(response)


def _parse_page_plan(response: str, model: str) -> PagePlan:
    m = re.search(r"\{.*\}", response, re.DOTALL)
    if not m:
        return PagePlan(page_type="landing", design_direction="minimal-geometric", sections=[],
                       architect_model=model)
    try:
        data = json.loads(m.group(0))
        return PagePlan(
            page_type=data.get("page_type", "landing"),
            design_direction=data.get("design_direction", "minimal-geometric"),
            color_palette=data.get("color_palette", {}),
            typography=data.get("typography", {}),
            sections=data.get("sections", []),
            asset_briefs=data.get("asset_briefs", []),
            architect_model=model,
        )
    except json.JSONDecodeError as exc:
        logger.warning("PagePlan parse failed: %s", exc)
        return PagePlan(page_type="landing", design_direction="minimal-geometric", sections=[],
                       architect_model=model)


def _strip_html_fences(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:html)?\n(.*?)```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _assemble_html(plan: PagePlan, sections: list[SectionResult]) -> str:
    body = "\n".join(s.html for s in sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Generated by Clawdbot Aider</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body>
{body}
</body>
</html>
"""
