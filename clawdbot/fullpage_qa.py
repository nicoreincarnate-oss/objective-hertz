"""Full-page visual QA pipeline for ClawdBot v2.

5-stage QA: multi-viewport screenshots, per-viewport VLM scoring,
markup checks, Claude Vision rubric, anti-slop text check.
Includes iteration (re-run failing sections) and deploy gate.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

try:
    from clawdbot.renderer import PlaywrightPool
except ImportError:
    PlaywrightPool = None  # type: ignore[assignment,misc]

try:
    from clawdbot.section_planner import BuildPlan
except ImportError:
    BuildPlan = None  # type: ignore[assignment,misc]

try:
    from clawdbot.site_quality import analyze_site_markup
except (ImportError, TypeError):
    def analyze_site_markup(html: str, **kwargs: Any) -> dict[str, Any]:  # type: ignore[misc]
        return {"score": 0.7, "issues": [], "passed": True}

try:
    from shared.anti_slop import AntiSlopScorer
except (ImportError, TypeError):
    AntiSlopScorer = None  # type: ignore[assignment]

logger = logging.getLogger("perseus.clawdbot.fullpage_qa")

# Feature flags
VISUAL_QA_BLOCKING = os.environ.get("VISUAL_QA_BLOCKING", "false").lower() == "true"
VISUAL_QA_MIN_THRESHOLD = float(os.environ.get("VISUAL_QA_MIN_THRESHOLD", "7.0"))


# ---------------------------------------------------------------------------
# Task 3: Full-Page QA Result + Pipeline
# ---------------------------------------------------------------------------


@dataclass
class FullPageQAResult:
    """Complete QA result for an assembled page."""

    passed: bool = False
    overall_score: float = 0.0
    rubric_scores: dict[str, float] = field(default_factory=dict)
    viewport_scores: dict[str, float] = field(default_factory=dict)
    markup_check: dict[str, Any] = field(default_factory=dict)
    anti_slop_check: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    screenshots: dict[str, bytes] = field(default_factory=dict)


async def run_full_page_qa(
    html: str,
    build_plan: Any,
    pool: Any | None = None,
) -> FullPageQAResult:
    """5-stage QA pipeline for assembled page.

    Stage 1: Multi-viewport screenshots (desktop/tablet/mobile)
    Stage 2: Per-viewport VLM quality scoring
    Stage 3: Markup checks (existing analyze_site_markup)
    Stage 4: Claude Vision 10-point rubric (premium tier only)
    Stage 5: Anti-slop text quality check

    Aggregates all scores into pass/fail verdict.
    Pass if overall >= 7.0 AND mandatory items pass.

    Args:
        html: Complete HTML page string.
        build_plan: BuildPlan with design tokens and business context.
        pool: Optional PlaywrightPool for rendering screenshots.

    Returns:
        FullPageQAResult with scores, issues, and screenshots.
    """
    result = FullPageQAResult()
    all_issues: list[str] = []
    all_recommendations: list[str] = []

    business_context = getattr(build_plan, "business_context", {})
    design_tokens = getattr(build_plan, "design_tokens", None)
    tier = getattr(build_plan, "tier", None)
    tier_name = getattr(tier, "name", "demo") if tier else "demo"

    # ----- Stage 1: Multi-viewport screenshots -----
    screenshots: dict[str, bytes] = {}
    if pool is not None:
        try:
            screenshots = await pool.render_multi_viewport(html)
            result.screenshots = screenshots
        except Exception:
            logger.warning("Multi-viewport rendering failed", exc_info=True)
            all_issues.append("Screenshot rendering failed - skipping visual checks")

    # ----- Stage 2: Per-viewport VLM quality scoring -----
    viewport_scores: dict[str, float] = {}
    if screenshots and design_tokens is not None:
        try:
            from clawdbot.visual_scorer import score_section_quality

            for vp_name, png_bytes in screenshots.items():
                try:
                    score = await score_section_quality(
                        png_bytes, f"fullpage_{vp_name}", design_tokens,
                    )
                    viewport_scores[vp_name] = score.overall
                    if score.issues:
                        all_issues.extend(
                            f"[{vp_name}] {issue}" for issue in score.issues
                        )
                    if score.actionable_fixes:
                        all_recommendations.extend(score.actionable_fixes)
                except Exception:
                    logger.warning("VLM scoring failed for %s viewport", vp_name, exc_info=True)
                    viewport_scores[vp_name] = 0.0
        except ImportError:
            logger.debug("visual_scorer not available; skipping VLM scoring")

    result.viewport_scores = viewport_scores

    # ----- Stage 3: Markup checks -----
    try:
        business_name = business_context.get("name", "")
        site_type = getattr(build_plan, "site_type", "demo")
        markup_result = analyze_site_markup(
            html, business_name=business_name, site_type=site_type,
        )
        result.markup_check = markup_result
        markup_issues = markup_result.get("issues", [])
        if isinstance(markup_issues, list):
            all_issues.extend(markup_issues)
    except Exception:
        logger.warning("Markup analysis failed", exc_info=True)
        result.markup_check = {"error": "analysis failed"}

    # ----- Stage 4: Claude Vision 10-point rubric (premium tier only) -----
    rubric_scores: dict[str, float] = {}
    if tier_name == "premium" and screenshots and design_tokens is not None:
        try:
            from clawdbot.visual_scorer import score_full_page

            full_score = await score_full_page(
                screenshots, design_tokens, business_context,
            )
            rubric_scores = {
                "color_consistency": full_score.color_consistency,
                "typography_hierarchy": full_score.typography_hierarchy,
                "spacing_consistency": full_score.spacing_consistency,
                "responsive_mobile": full_score.responsive_mobile,
                "responsive_tablet": full_score.responsive_tablet,
                "navigation": full_score.navigation,
                "above_fold_impact": full_score.above_fold_impact,
                "content_hierarchy": full_score.content_hierarchy,
                "animation_presence": full_score.animation_presence,
                "aesthetic_cohesion": full_score.aesthetic_cohesion,
            }
            if full_score.issues:
                all_issues.extend(full_score.issues)

            # Check mandatory rubric items
            if full_score.responsive_mobile < 0.5:
                all_issues.append("MANDATORY FAIL: responsive_mobile below 0.5")
            if full_score.navigation < 0.5:
                all_issues.append("MANDATORY FAIL: navigation below 0.5")

        except ImportError:
            logger.debug("visual_scorer not available; skipping full-page rubric")
        except Exception:
            logger.warning("Full-page rubric scoring failed", exc_info=True)

    result.rubric_scores = rubric_scores

    # ----- Stage 5: Anti-slop text check -----
    anti_slop_result: dict[str, Any] = {}
    visible_text = _extract_visible_text(html)

    if AntiSlopScorer is not None:
        try:
            scorer = AntiSlopScorer()
            slop_result = scorer.score(visible_text)
            anti_slop_result = {
                "slop_score": getattr(slop_result, "slop_score", 0),
                "flagged_phrases": getattr(slop_result, "flagged_phrases", []),
                "passed": getattr(slop_result, "slop_score", 0) < 0.3,
            }
        except Exception:
            logger.debug("AntiSlopScorer failed; using regex fallback", exc_info=True)
            anti_slop_result = _regex_slop_check(visible_text)
    else:
        anti_slop_result = _regex_slop_check(visible_text)

    result.anti_slop_check = anti_slop_result
    if not anti_slop_result.get("passed", True):
        flagged = anti_slop_result.get("flagged_phrases", [])
        if flagged:
            all_issues.append(f"Anti-slop: {len(flagged)} slop phrases detected")

    # ----- Check mandatory blockers -----
    mandatory_pass = True

    # Placeholder content check
    placeholder_patterns = [
        "lorem ipsum", "coming soon", "your business here",
        "placeholder", "sample text", "dummy text",
    ]
    lower_text = visible_text.lower()
    for pattern in placeholder_patterns:
        if pattern in lower_text:
            all_issues.append(f"MANDATORY FAIL: placeholder content detected: '{pattern}'")
            mandatory_pass = False
            break

    # Secrets check
    secret_patterns = [
        r"sk_live_[a-zA-Z0-9]+",
        r"sk_test_[a-zA-Z0-9]+",
        r"pk_live_[a-zA-Z0-9]+",
        r"AKIA[A-Z0-9]{16}",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, html):
            all_issues.append("MANDATORY FAIL: secrets detected in HTML")
            mandatory_pass = False
            break

    # No HTML check
    if not html or not html.strip():
        all_issues.append("MANDATORY FAIL: empty HTML")
        mandatory_pass = False

    # ----- Aggregate scores -----
    score_components: list[float] = []

    # Viewport scores (normalized to 0-10)
    if viewport_scores:
        avg_viewport = sum(viewport_scores.values()) / len(viewport_scores)
        score_components.append(avg_viewport)

    # Rubric scores (each 0-1, total 0-10)
    if rubric_scores:
        rubric_total = sum(rubric_scores.values())
        score_components.append(rubric_total)

    # Markup score (normalize to 0-10)
    markup_score = result.markup_check.get("score", 0.7)
    if isinstance(markup_score, (int, float)):
        score_components.append(float(markup_score) * 10)

    if score_components:
        overall_score = sum(score_components) / len(score_components)
    else:
        # Fallback: basic HTML checks only
        overall_score = 5.0 if html.strip() else 0.0

    result.overall_score = round(overall_score, 2)
    result.issues = all_issues
    result.recommendations = all_recommendations

    # Pass/fail verdict
    result.passed = (
        overall_score >= VISUAL_QA_MIN_THRESHOLD
        and mandatory_pass
    )

    return result


def _extract_visible_text(html_str: str) -> str:
    """Extract visible text from HTML, stripping tags and scripts."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html_str, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


_SLOP_PATTERNS = [
    r"I hope this (?:email|message) finds you well",
    r"\bunlock the full potential\b",
    r"\btake (?:it |things )?to the next level\b",
    r"\bin today'?s (?:fast[- ]paced|competitive|digital|modern)\b",
    r"\beverything you need\b",
    r"\bgame[- ]?changer\b",
    r"\bsynergy\b",
    r"\bleverage\b",
]


def _regex_slop_check(text: str) -> dict[str, Any]:
    """Fallback regex-based slop detection."""
    flagged: list[str] = []
    for pattern in _SLOP_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        flagged.extend(matches)
    slop_score = min(len(flagged) / 5.0, 1.0)
    return {
        "slop_score": slop_score,
        "flagged_phrases": flagged,
        "passed": slop_score < 0.3,
    }


# ---------------------------------------------------------------------------
# Task 4: Full-Page Iteration
# ---------------------------------------------------------------------------


async def iterate_full_page(
    html: str,
    qa_result: FullPageQAResult,
    build_plan: Any,
    sections: dict[str, Any],
    pool: Any | None = None,
    max_iterations: int = 2,
) -> tuple[str, FullPageQAResult]:
    """Fix failing sections identified by full-page QA.

    1. Parse QA issues to identify which sections caused them
    2. Re-run those sections through section_agent with QA feedback
    3. Re-assemble page with fixed sections
    4. Re-run full-page QA
    5. Max 2 full-page iterations

    Args:
        html: Current assembled HTML.
        qa_result: QA result identifying issues.
        build_plan: BuildPlan for rebuilding.
        sections: Current section results dict.
        pool: Optional PlaywrightPool.
        max_iterations: Maximum iteration attempts.

    Returns:
        Tuple of (improved_html, final_qa_result).
    """
    current_html = html
    current_qa = qa_result
    current_sections = dict(sections)

    for iteration in range(max_iterations):
        if current_qa.passed:
            break

        # Identify failing sections from issues
        failing_sections = _identify_failing_sections(current_qa.issues, current_sections)

        if not failing_sections:
            logger.info("No specific failing sections identified; cannot iterate further")
            break

        logger.info(
            "Full-page iteration %d: re-running %d sections: %s",
            iteration + 1, len(failing_sections), failing_sections,
        )

        # Re-generate failing sections
        try:
            from clawdbot.section_agent import generate_section

            plan_sections = getattr(build_plan, "sections", [])
            contract = getattr(build_plan, "design_contract", {})
            design_tokens = getattr(build_plan, "design_tokens", None)
            tier = getattr(build_plan, "tier", None)

            for section_type in failing_sections:
                # Find the section plan
                section_plan = None
                for sp in plan_sections:
                    if getattr(sp, "section_type", "") == section_type:
                        section_plan = sp
                        break

                if section_plan is None:
                    continue

                # Re-generate with QA feedback
                feedback_issues = [
                    issue for issue in current_qa.issues
                    if section_type.lower() in issue.lower()
                    or "mandatory" in issue.lower()
                ]
                if not feedback_issues:
                    feedback_issues = current_qa.issues[:3]

                # Inject feedback into content
                content = getattr(section_plan, "content", {})
                content["_qa_feedback"] = feedback_issues

                new_result = await generate_section(
                    plan=section_plan,
                    contract=contract,
                    tokens=design_tokens,
                    pool=pool,
                    tier=tier,
                )

                if new_result.passed or (new_result.html and new_result.html.strip()):
                    current_sections[section_type] = new_result

        except ImportError:
            logger.warning("section_agent not available; cannot iterate")
            break
        except Exception:
            logger.warning("Section re-generation failed in iteration %d", iteration + 1, exc_info=True)
            break

        # Re-assemble page
        try:
            from clawdbot.page_assembler import assemble_page
            current_html = await assemble_page(current_sections, build_plan)
        except Exception:
            logger.warning("Re-assembly failed in iteration %d", iteration + 1, exc_info=True)
            break

        # Re-run QA
        current_qa = await run_full_page_qa(current_html, build_plan, pool=pool)

    return current_html, current_qa


def _identify_failing_sections(
    issues: list[str],
    sections: dict[str, Any],
) -> list[str]:
    """Map QA issues to specific section types."""
    failing: set[str] = set()
    section_types = list(sections.keys())

    # Pattern-based mapping
    issue_section_map: dict[str, list[str]] = {
        "horizontal overflow": ["hero", "features", "services", "pricing"],
        "navigation": ["navbar"],
        "nav ": ["navbar"],
        "footer": ["footer"],
        "hero": ["hero"],
        "typography": [],  # could be any section
        "color mismatch": [],
        "responsive_mobile": ["hero", "features", "services"],
        "responsive_tablet": ["hero", "features", "services"],
        "above_fold": ["hero"],
        "placeholder": [],  # need to scan all sections
    }

    for issue in issues:
        issue_lower = issue.lower()

        # Direct section type mentions
        for stype in section_types:
            if stype.lower() in issue_lower:
                failing.add(stype)

        # Pattern-based detection
        for pattern, mapped_sections in issue_section_map.items():
            if pattern in issue_lower:
                if mapped_sections:
                    for ms in mapped_sections:
                        if ms in section_types:
                            failing.add(ms)
                else:
                    # Issue applies broadly; add all main sections
                    for stype in section_types:
                        if stype not in ("navbar", "footer"):
                            failing.add(stype)
                    break

    return list(failing)


# ---------------------------------------------------------------------------
# Task 5: Deploy Gate
# ---------------------------------------------------------------------------


async def deploy_gate(qa_result: FullPageQAResult) -> tuple[bool, str]:
    """Decide whether to deploy based on QA results.

    Hard gate (VISUAL_QA_BLOCKING=true):
      - Deploy only if overall_score >= VISUAL_QA_MIN_THRESHOLD
      - AND all mandatory checks pass (responsive, nav, no placeholders)

    Soft gate (VISUAL_QA_BLOCKING=false, default):
      - Always deploy
      - Log warning if score < threshold
      - Emit event for monitoring

    Mandatory blocks regardless of gate mode:
      - Placeholder content detected
      - Secrets detected in HTML
      - No HTML / empty page

    Returns:
        Tuple of (should_deploy, reason).
    """
    threshold = VISUAL_QA_MIN_THRESHOLD

    # Check mandatory blocks (apply in both hard and soft mode)
    mandatory_blocks: list[str] = []
    for issue in qa_result.issues:
        if "MANDATORY FAIL" in issue:
            mandatory_blocks.append(issue)

    if mandatory_blocks:
        reason = f"Blocked by mandatory checks: {'; '.join(mandatory_blocks)}"
        logger.warning("Deploy gate: %s", reason)
        return False, reason

    # Hard gate mode
    if VISUAL_QA_BLOCKING:
        if qa_result.overall_score < threshold:
            reason = (
                f"Hard gate: score {qa_result.overall_score:.1f} "
                f"below threshold {threshold:.1f}"
            )
            logger.warning("Deploy gate: %s", reason)
            return False, reason

        if not qa_result.passed:
            reason = "Hard gate: QA did not pass all checks"
            logger.warning("Deploy gate: %s", reason)
            return False, reason

        return True, f"Passed hard gate with score {qa_result.overall_score:.1f}"

    # Soft gate mode (default)
    if qa_result.overall_score < threshold:
        reason = (
            f"Soft gate: deploying with warning - score {qa_result.overall_score:.1f} "
            f"below threshold {threshold:.1f}"
        )
        logger.warning("Deploy gate: %s", reason)
        # Emit monitoring event
        _emit_low_score_event(qa_result.overall_score, threshold, qa_result.issues[:5])
        return True, reason

    return True, f"Passed soft gate with score {qa_result.overall_score:.1f}"


def _emit_low_score_event(score: float, threshold: float, issues: list[str]) -> None:
    """Fire-and-forget event for low deploy scores."""
    try:
        import asyncio

        from shared.db import emit_event
        asyncio.ensure_future(emit_event("deploy_low_score", {
            "score": score,
            "threshold": threshold,
            "issues": issues,
        }))
    except Exception:
        pass
