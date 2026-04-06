"""VLM-based visual scoring pipeline for ClawdBot site generation.

Provides three scoring modes:
- Section quality scoring (per-section during generation)
- Reference comparison (against curated reference screenshots)
- Full-page 10-point rubric (final QA)

Supports Ollama (Qwen2.5-VL, free) and Claude Vision (paid fallback).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clawdbot.design_tokens import DesignTokens
    from clawdbot.renderer import PlaywrightPool

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

VISUAL_QA_ENABLED = os.environ.get("VISUAL_QA_ENABLED", "true").lower() == "true"
VISUAL_QA_VLM_PROVIDER = os.environ.get("VISUAL_QA_VLM_PROVIDER", "ollama")

# ---------------------------------------------------------------------------
# Score dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SectionScore:
    """Structured result from per-section visual quality scoring."""

    overall: float = 0.0
    hierarchy: float = 0.0
    spacing: float = 0.0
    typography: float = 0.0
    color_usage: float = 0.0
    component_quality: float = 0.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = False


@dataclass
class ComparisonScore:
    """Structured result from reference comparison."""

    match_score: float = 0.0
    color_match: float = 0.0
    typography_match: float = 0.0
    layout_match: float = 0.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = False


@dataclass
class FullPageScore:
    """10-point rubric for final full-page QA."""

    total: float = 0.0
    color_consistency: float = 0.0
    typography_hierarchy: float = 0.0
    spacing_consistency: float = 0.0
    responsive_mobile: float = 0.0
    responsive_tablet: float = 0.0
    navigation: float = 0.0
    above_fold_impact: float = 0.0
    content_hierarchy: float = 0.0
    animation_presence: float = 0.0
    aesthetic_cohesion: float = 0.0
    mandatory_pass: bool = False
    issues: list[str] = field(default_factory=list)


@dataclass
class ContractValidation:
    """Result of design contract (token palette) validation."""

    passed: bool = False
    contrast_ok: bool = False
    readability_ok: bool = False
    aesthetic_ok: bool = False
    issues: list[str] = field(default_factory=list)
    suggested_adjustments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# VLM backend abstraction (Task 3)
# ---------------------------------------------------------------------------


def _parse_vlm_json(raw: str) -> dict[str, Any]:
    """Parse JSON from VLM response, handling markdown fences and embedded JSON.

    VLMs often wrap JSON in markdown code fences or embed it in
    explanatory text.  This function handles all those cases.
    """
    text = raw.strip()

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Find the outermost {} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    logger.warning("Failed to parse VLM JSON: %.200s", text)
    return {}


async def _vlm_score(
    images: list[bytes],
    prompt: str,
    provider: str | None = None,
) -> dict[str, Any]:
    """Send images to a VLM and parse structured JSON response.

    Args:
        images: List of PNG image bytes.
        prompt: Scoring prompt requesting JSON output.
        provider: ``"ollama"`` or ``"claude"``.  Defaults to feature flag.

    Returns:
        Parsed dict from VLM JSON, or fallback dict on total failure.
    """
    provider = provider or VISUAL_QA_VLM_PROVIDER

    # Try primary provider
    try:
        if provider == "ollama":
            return await _vlm_ollama(images, prompt)
        return await _vlm_claude(images, prompt)
    except Exception:
        logger.warning("Primary VLM (%s) failed; trying fallback", provider, exc_info=True)

    # Fallback to the other provider
    try:
        if provider == "ollama":
            return await _vlm_claude(images, prompt)
        return await _vlm_ollama(images, prompt)
    except Exception:
        logger.error("Both VLM providers failed", exc_info=True)
        return {"raw": "", "model": "none"}


async def _vlm_ollama(images: list[bytes], prompt: str) -> dict[str, Any]:
    """Score images via Ollama vision model (Qwen2.5-VL)."""
    if httpx is None:
        raise RuntimeError("httpx is required for Ollama VLM calls")

    from shared.config import config

    images_b64 = [base64.b64encode(img).decode() for img in images]
    model = getattr(config.ollama, "vision_model", "qwen2.5vl:7b")
    host = config.ollama.host

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": images_b64,
                    }
                ],
                "stream": False,
                "format": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = data.get("message", {}).get("content", "")
    parsed = _parse_vlm_json(content)
    parsed["model"] = model
    return parsed


async def _vlm_claude(images: list[bytes], prompt: str) -> dict[str, Any]:
    """Score images via Claude Vision (shared LLM client)."""
    from shared.llm_client import llm

    result = await llm.generate_with_images(
        prompt,
        images=images,
        model="smart",
        max_tokens=1500,
        temperature=0.1,
    )
    parsed = _parse_vlm_json(result)
    parsed["model"] = "claude-vision"
    return parsed


# ---------------------------------------------------------------------------
# Prompt A: Section quality score
# ---------------------------------------------------------------------------

_SECTION_QUALITY_PROMPT = textwrap.dedent("""\
    You are a web design quality inspector. Evaluate this screenshot of a
    "{section_type}" website section.

    Design tokens in effect:
    - Primary: {primary}, Accent: {accent}, Background: {background}
    - Display font: {font_display}, Body font: {font_body}
    - Direction: {direction}

    Score each dimension 0-10 and identify specific issues with actionable fixes.

    Return ONLY valid JSON:
    {{
        "overall": <float 0-10>,
        "hierarchy": <float 0-10>,
        "spacing": <float 0-10>,
        "typography": <float 0-10>,
        "color_usage": <float 0-10>,
        "component_quality": <float 0-10>,
        "issues": ["specific problem 1", "..."],
        "actionable_fixes": ["CSS/HTML change 1", "..."]
    }}
""")


async def score_section_quality(
    screenshot: bytes,
    section_type: str,
    design_tokens: DesignTokens,
) -> SectionScore:
    """Score a generated section screenshot for visual quality.

    Args:
        screenshot: PNG bytes of the rendered section.
        section_type: E.g. "hero", "features", "pricing".
        design_tokens: Current design token set.

    Returns:
        Structured ``SectionScore`` with per-dimension scores and issues.
    """
    prompt = _SECTION_QUALITY_PROMPT.format(
        section_type=section_type,
        primary=design_tokens.primary,
        accent=design_tokens.accent,
        background=design_tokens.background,
        font_display=design_tokens.font_display,
        font_body=design_tokens.font_body,
        direction=design_tokens.direction_name,
    )

    data = await _vlm_score([screenshot], prompt)

    overall = float(data.get("overall", 0))
    return SectionScore(
        overall=overall,
        hierarchy=float(data.get("hierarchy", 0)),
        spacing=float(data.get("spacing", 0)),
        typography=float(data.get("typography", 0)),
        color_usage=float(data.get("color_usage", 0)),
        component_quality=float(data.get("component_quality", 0)),
        issues=data.get("issues", []),
        actionable_fixes=data.get("actionable_fixes", []),
        passed=overall >= 7.0,
    )


# ---------------------------------------------------------------------------
# Prompt B: Reference comparison
# ---------------------------------------------------------------------------

_REFERENCE_COMPARISON_PROMPT = textwrap.dedent("""\
    You are comparing a generated "{section_type}" website section (Image 1)
    against a reference design (Image 2).

    Evaluate how well the generated section matches the reference aesthetic.
    Score each dimension 0-1 (1 = perfect match).

    Return ONLY valid JSON:
    {{
        "match_score": <float 0-1>,
        "color_match": <float 0-1>,
        "typography_match": <float 0-1>,
        "layout_match": <float 0-1>,
        "issues": ["deviation 1", "..."],
        "actionable_fixes": ["CSS/HTML change 1", "..."]
    }}
""")


async def compare_to_reference(
    generated: bytes,
    reference: bytes,
    section_type: str,
) -> ComparisonScore:
    """Compare generated section against a reference aesthetic.

    Args:
        generated: PNG bytes of the generated section.
        reference: PNG bytes of the reference design.
        section_type: E.g. "hero", "features".

    Returns:
        Structured ``ComparisonScore`` with match dimensions.
    """
    prompt = _REFERENCE_COMPARISON_PROMPT.format(section_type=section_type)

    data = await _vlm_score([generated, reference], prompt)

    match = float(data.get("match_score", 0))
    return ComparisonScore(
        match_score=match,
        color_match=float(data.get("color_match", 0)),
        typography_match=float(data.get("typography_match", 0)),
        layout_match=float(data.get("layout_match", 0)),
        issues=data.get("issues", []),
        actionable_fixes=data.get("actionable_fixes", []),
        passed=match >= 0.75,
    )


# ---------------------------------------------------------------------------
# Prompt C: Full-page 10-point rubric
# ---------------------------------------------------------------------------

_FULL_PAGE_RUBRIC_PROMPT = textwrap.dedent("""\
    You are a comprehensive web design evaluator scoring a complete website.
    You will see screenshots at desktop, tablet, and mobile viewports.

    Business context: {business_context}
    Design direction: {direction}
    Primary: {primary}, Accent: {accent}

    Score each of these 10 dimensions 0-1 (1 = excellent):
    1. color_consistency — Colors consistent across all sections
    2. typography_hierarchy — Clear H1 > H2 > body hierarchy
    3. spacing_consistency — Uniform padding and margins
    4. responsive_mobile — No horizontal overflow at 375px
    5. responsive_tablet — No layout breaks at 768px
    6. navigation — Functional links, mobile menu present
    7. above_fold_impact — Hero is visually compelling
    8. content_hierarchy — Clear CTA above the fold
    9. animation_presence — Scroll-triggered animations present
    10. aesthetic_cohesion — Cohesive, not a Frankenstein assembly

    Return ONLY valid JSON:
    {{
        "color_consistency": <float 0-1>,
        "typography_hierarchy": <float 0-1>,
        "spacing_consistency": <float 0-1>,
        "responsive_mobile": <float 0-1>,
        "responsive_tablet": <float 0-1>,
        "navigation": <float 0-1>,
        "above_fold_impact": <float 0-1>,
        "content_hierarchy": <float 0-1>,
        "animation_presence": <float 0-1>,
        "aesthetic_cohesion": <float 0-1>,
        "issues": ["problem 1", "..."]
    }}
""")


async def score_full_page(
    screenshots: dict[str, bytes],
    design_tokens: DesignTokens,
    business_context: dict[str, Any],
) -> FullPageScore:
    """10-point rubric scoring for the final assembled page.

    Args:
        screenshots: Dict mapping viewport name (desktop/tablet/mobile)
            to PNG bytes.
        design_tokens: Current design token set.
        business_context: Business info dict (name, industry, etc.).

    Returns:
        Structured ``FullPageScore`` with 10 dimensions.
    """
    prompt = _FULL_PAGE_RUBRIC_PROMPT.format(
        business_context=json.dumps(business_context, ensure_ascii=False, default=str),
        direction=design_tokens.direction_name,
        primary=design_tokens.primary,
        accent=design_tokens.accent,
    )

    images = [
        screenshots.get("desktop", b""),
        screenshots.get("tablet", b""),
        screenshots.get("mobile", b""),
    ]
    images = [img for img in images if img]

    data = await _vlm_score(images, prompt)

    dimensions = [
        "color_consistency",
        "typography_hierarchy",
        "spacing_consistency",
        "responsive_mobile",
        "responsive_tablet",
        "navigation",
        "above_fold_impact",
        "content_hierarchy",
        "animation_presence",
        "aesthetic_cohesion",
    ]

    scores = {dim: float(data.get(dim, 0)) for dim in dimensions}
    total = sum(scores.values())

    # Mandatory: responsive + navigation must pass (>= 0.5 each)
    mandatory_pass = (
        scores["responsive_mobile"] >= 0.5
        and scores["responsive_tablet"] >= 0.5
        and scores["navigation"] >= 0.5
    )

    return FullPageScore(
        total=total,
        mandatory_pass=mandatory_pass,
        issues=data.get("issues", []),
        **scores,
    )


# ---------------------------------------------------------------------------
# Design contract validator (Task 4)
# ---------------------------------------------------------------------------

_CONTRACT_TEST_HTML = textwrap.dedent("""\
    <section style="padding: var(--section-padding-y, 6rem) var(--container-padding-x, 1.5rem); max-width: var(--container-max-width, 1280px); margin: 0 auto;">
        <h1 style="font-family: var(--font-display), system-ui; font-size: 3.5rem; font-weight: var(--heading-weight, 700); line-height: var(--heading-line-height, 1.1); letter-spacing: var(--letter-spacing-heading, -0.02em); color: var(--color-text-primary); margin-bottom: 1.5rem;">
            Design Contract Validation
        </h1>
        <p style="font-family: var(--font-body), system-ui; font-size: 1.125rem; line-height: var(--body-line-height, 1.6); color: var(--color-text-secondary); max-width: 600px; margin-bottom: 2rem;">
            This test section validates that the design tokens produce readable,
            visually coherent output with sufficient contrast and clear hierarchy.
        </p>
        <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
            <button style="background: var(--color-accent); color: white; padding: 0.75rem 2rem; border: none; border-radius: var(--radius-md, 0.75rem); font-family: var(--font-body); font-weight: 600; font-size: 1rem; cursor: pointer;">
                Primary Action
            </button>
            <button style="background: transparent; color: var(--color-text-primary); padding: 0.75rem 2rem; border: 2px solid var(--color-text-primary); border-radius: var(--radius-md, 0.75rem); font-family: var(--font-body); font-weight: 500; font-size: 1rem; cursor: pointer;">
                Secondary Action
            </button>
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--card-gap, 2rem); margin-top: 3rem;">
            <div style="background: var(--color-surface); padding: 2rem; border-radius: var(--radius-lg, 1rem);">
                <h3 style="font-family: var(--font-display); font-size: 1.25rem; font-weight: 600; color: var(--color-text-primary); margin-bottom: 0.5rem;">Card Title</h3>
                <p style="font-family: var(--font-body); color: var(--color-text-secondary); font-size: 0.875rem;">Card body text to validate secondary color contrast and readability at smaller sizes.</p>
            </div>
            <div style="background: var(--color-surface); padding: 2rem; border-radius: var(--radius-lg, 1rem);">
                <h3 style="font-family: var(--font-display); font-size: 1.25rem; font-weight: 600; color: var(--color-text-primary); margin-bottom: 0.5rem;">Second Card</h3>
                <p style="font-family: var(--font-body); color: var(--color-text-muted); font-size: 0.875rem;">Muted text color validation for less prominent content.</p>
            </div>
            <div style="background: var(--color-accent); padding: 2rem; border-radius: var(--radius-lg, 1rem);">
                <h3 style="font-family: var(--font-display); font-size: 1.25rem; font-weight: 600; color: white; margin-bottom: 0.5rem;">Accent Card</h3>
                <p style="font-family: var(--font-body); color: rgba(255,255,255,0.85); font-size: 0.875rem;">White text on accent background — contrast check.</p>
            </div>
        </div>
    </section>
""")

_CONTRACT_VALIDATION_PROMPT = textwrap.dedent("""\
    You are validating a design system's token palette. This screenshot shows a
    test section rendered with the design tokens. Check for:

    1. **Contrast**: Is all text readable against its background? Pay attention
       to muted text, secondary text, and white-on-accent combinations.
    2. **Readability**: Are font sizes appropriate? Is line height comfortable?
       Can you read all text without straining?
    3. **Aesthetic coherence**: Do the colors, fonts, and spacing feel cohesive?
       Or does it look like mismatched pieces?

    Return ONLY valid JSON:
    {{
        "contrast_ok": <bool>,
        "readability_ok": <bool>,
        "aesthetic_ok": <bool>,
        "issues": ["specific problem 1", "..."],
        "suggested_adjustments": ["fix 1", "..."]
    }}
""")


async def validate_design_contract(
    tokens: DesignTokens,
    pool: PlaywrightPool,
) -> ContractValidation:
    """Render a test section with design tokens and VLM-check the result.

    Catches bad palettes (low contrast, illegible combinations) before they
    propagate to all sections.

    Args:
        tokens: DesignTokens to validate.
        pool: PlaywrightPool for rendering.

    Returns:
        ``ContractValidation`` with pass/fail and suggested adjustments.
    """
    from clawdbot.renderer import render_section_in_page

    screenshot = await render_section_in_page(
        _CONTRACT_TEST_HTML,
        tokens,
        pool=pool,
    )

    data = await _vlm_score([screenshot], _CONTRACT_VALIDATION_PROMPT)

    contrast_ok = bool(data.get("contrast_ok", False))
    readability_ok = bool(data.get("readability_ok", False))
    aesthetic_ok = bool(data.get("aesthetic_ok", False))

    return ContractValidation(
        passed=contrast_ok and readability_ok and aesthetic_ok,
        contrast_ok=contrast_ok,
        readability_ok=readability_ok,
        aesthetic_ok=aesthetic_ok,
        issues=data.get("issues", []),
        suggested_adjustments=data.get("suggested_adjustments", []),
    )
