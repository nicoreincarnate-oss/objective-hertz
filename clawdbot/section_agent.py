"""Section generation agent for ClawdBot v2.

Generates a single website section with VLM visual feedback loop.
Renders via Playwright, scores with VLM (Qwen2.5-VL / Claude Vision),
and iterates (patch or regenerate) until quality passes.
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clawdbot.design_tokens import DesignTokens
    from clawdbot.renderer import PlaywrightPool
    from clawdbot.section_planner import BuildTier, SectionPlan
    from clawdbot.visual_scorer import SectionScore

logger = logging.getLogger("perseus.clawdbot.section_agent")

# Feature flags
SECTION_MAX_ITERATIONS = int(os.environ.get("SECTION_MAX_ITERATIONS", "3"))
SECTION_PASS_THRESHOLD = float(os.environ.get("SECTION_PASS_THRESHOLD", "7.0"))

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SectionResult:
    """Result of generating a single section."""

    section_type: str
    html: str
    screenshot: bytes | None
    score: SectionScore | None
    iterations: int
    passed: bool
    error: str | None
    generation_tokens: int = 0
    generation_cost_usd: float = 0.0
    total_time_s: float = 0.0


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_section_prompt(
    plan: SectionPlan,
    contract: dict[str, Any],
    tokens: DesignTokens,
    snippet: str | None = None,
    previous_feedback: list[str] | None = None,
) -> str:
    """Build the LLM prompt for generating a single section.

    Includes design contract, tokens as CSS vars, business content,
    snippet example, and anti-slop rules.
    """
    palette = contract.get("palette", {})
    typography = contract.get("typography", {})
    spacing = contract.get("spacing", {})
    brand = contract.get("brand", {})

    palette_block = "\n".join(f"  --color-{k}: {v};" for k, v in palette.items())
    typo_block = "\n".join(f"  {k}: {v}" for k, v in typography.items())
    spacing_block = "\n".join(f"  {k}: {v}" for k, v in spacing.items())

    content_json = _format_content(plan.content)

    sections = [
        f"Generate a `{plan.section_type}` website section.",
        "",
        "## Design Contract (shared across all sections)",
        f"Brand: {brand.get('name', '')} ({brand.get('industry', '')})",
        "",
        "### Color Palette (use these CSS custom properties)",
        f"```css\n:root {{\n{palette_block}\n}}\n```",
        "",
        "### Typography",
        typo_block,
        "",
        "### Spacing",
        spacing_block,
        "",
        "## Section Content",
        content_json,
    ]

    if snippet:
        sections.extend([
            "",
            "## Structural Inspiration (do NOT copy, use as layout reference only)",
            f"```html\n{snippet}\n```",
        ])

    if previous_feedback:
        sections.extend([
            "",
            "## Previous Feedback (fix these issues)",
        ])
        for i, fb in enumerate(previous_feedback, 1):
            sections.append(f"{i}. {fb}")

    sections.extend([
        "",
        "## Rules",
        "- Use Tailwind CSS utility classes for ALL styling.",
        "- Reference CSS custom properties (var(--color-primary), etc.) for colors.",
        "- Use semantic HTML (headings, paragraphs, links, lists).",
        "- NO placeholder URLs (no unsplash.com, picsum.photos, placehold.co).",
        "- NO Lorem ipsum or dummy text.",
        "- NO placeholder images. Use inline SVG icons or CSS shapes if decoration is needed.",
        "- Section must be responsive (mobile-friendly).",
        "- Output ONLY the <section>...</section> HTML. No explanations.",
    ])

    return "\n".join(sections)


def _format_content(content: dict[str, Any]) -> str:
    """Format section content dict into a readable prompt block."""
    import json
    # Remove internal keys
    clean = {k: v for k, v in content.items() if not k.startswith("_")}
    return json.dumps(clean, indent=2, default=str)


# ---------------------------------------------------------------------------
# HTML extraction and validation
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"<section[\s>].*?</section>",
    re.DOTALL | re.IGNORECASE,
)

_FENCE_RE = re.compile(r"```(?:html)?\s*\n?(.*?)```", re.DOTALL)

_PLACEHOLDER_URLS = re.compile(
    r"(?:unsplash\.com|picsum\.photos|placehold\.co|via\.placeholder|placeholder\.com|lorempixel\.com)",
    re.IGNORECASE,
)

_LOREM_RE = re.compile(r"lorem\s+ipsum", re.IGNORECASE)


def extract_section_html(raw_output: str) -> str | None:
    """Extract <section>...</section> from LLM output.

    Handles markdown fences, explanation text, multiple sections
    (takes the first), and bare HTML.
    """
    if not raw_output or not raw_output.strip():
        return None

    text = raw_output.strip()

    # Strip markdown fences first
    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Find <section>...</section>
    section_match = _SECTION_RE.search(text)
    if section_match:
        return section_match.group(0).strip()

    # If the whole output looks like HTML (starts with < but missing section tag),
    # wrap it
    stripped = text.strip()
    if stripped.startswith("<") and not stripped.lower().startswith("<section"):
        return f"<section>\n{stripped}\n</section>"

    return None


def validate_section_html(html: str, section_type: str) -> list[str]:
    """Pre-render validation checks for section HTML.

    Returns list of issues (empty = valid).
    """
    issues: list[str] = []

    if "<section" not in html.lower():
        issues.append("Missing <section> tag")

    # Check for Tailwind classes (at least a few class attributes)
    class_count = html.count("class=")
    if class_count < 2:
        issues.append(f"Too few class attributes ({class_count}); likely missing Tailwind styling")

    # Check for placeholder URLs
    if _PLACEHOLDER_URLS.search(html):
        issues.append("Contains placeholder image URLs (unsplash, picsum, etc.)")

    # Check for Lorem ipsum
    if _LOREM_RE.search(html):
        issues.append("Contains Lorem ipsum placeholder text")

    # Length checks
    length = len(html)
    if length < 200:
        issues.append(f"Section HTML too short ({length} chars); likely incomplete")
    elif length > 15000:
        issues.append(f"Section HTML too long ({length} chars); likely bloated")

    # Check for semantic elements based on section type
    if section_type in ("hero", "services", "features", "pricing"):
        if not re.search(r"<h[1-6]", html, re.IGNORECASE):
            issues.append(f"Section '{section_type}' should have heading elements")

    return issues


# ---------------------------------------------------------------------------
# Core generation function
# ---------------------------------------------------------------------------


async def generate_section(
    plan: SectionPlan,
    contract: dict[str, Any],
    tokens: DesignTokens,
    pool: PlaywrightPool | None = None,
    tier: BuildTier | None = None,
) -> SectionResult:
    """Generate a single section with VLM visual feedback loop.

    Flow:
    1. Build prompt from plan + contract + snippet example
    2. Call LLM to generate section HTML
    3. Extract and validate section HTML
    4. If pool available: render section in page context (Playwright)
    5. If rendered: score with VLM (score_section_quality)
    6. If score < threshold and iterations < max: iterate (patch or regenerate)
    7. Return SectionResult
    """
    t0 = time.monotonic()
    max_iter = plan.max_iterations
    if tier is not None:
        max_iter = min(max_iter, tier.max_iterations)
    max_iter = min(max_iter, SECTION_MAX_ITERATIONS)

    # Resolve model
    model = "smart"
    if tier is not None:
        model = tier.model

    # Load snippet if referenced
    snippet_html: str | None = None
    if plan.snippet_ref:
        try:
            from clawdbot.snippet_registry import load_snippet
            snip = load_snippet(plan.section_type, plan.snippet_ref)
            snippet_html = snip.get("html")
        except (FileNotFoundError, ImportError):
            logger.debug("Snippet %s/%s not found", plan.section_type, plan.snippet_ref)

    # Build initial prompt
    prompt = build_section_prompt(plan, contract, tokens, snippet=snippet_html)

    total_tokens = 0
    total_cost = 0.0
    current_html = ""
    screenshot: bytes | None = None
    score: SectionScore | None = None
    accumulated_feedback: list[str] = []
    last_error: str | None = None

    for iteration in range(1, max_iter + 1):
        try:
            # Generate HTML
            if iteration == 1:
                raw = await _call_llm(prompt, model=model)
            else:
                # Iteration: patch or regenerate
                if score is not None:
                    new_html = await _iterate_section(
                        current_html=current_html,
                        feedback=score,
                        plan=plan,
                        contract=contract,
                        tokens=tokens,
                        iteration=iteration,
                        tier=tier,
                    )
                    raw = new_html
                else:
                    raw = await _call_llm(prompt, model=model)

            # Estimate tokens (rough: 4 chars per token)
            est_tokens = max(len(raw) // 4, 100)
            total_tokens += est_tokens
            total_cost += _estimate_token_cost(model, est_tokens)

            # Extract HTML
            extracted = extract_section_html(raw)
            if extracted is None:
                last_error = "Failed to extract section HTML from LLM output"
                logger.warning(
                    "Section %s iter %d: %s",
                    plan.section_type, iteration, last_error,
                )
                continue

            # Validate
            validation_issues = validate_section_html(extracted, plan.section_type)
            if validation_issues:
                logger.info(
                    "Section %s iter %d validation: %s",
                    plan.section_type, iteration, validation_issues,
                )
                accumulated_feedback.extend(validation_issues)
                # Continue with the HTML anyway; VLM will catch visual issues
                current_html = extracted
            else:
                current_html = extracted

            # Render + score if pool available
            if pool is not None:
                try:
                    from clawdbot.renderer import render_section_in_page
                    from clawdbot.visual_scorer import score_section_quality

                    screenshot = await render_section_in_page(
                        current_html, tokens, pool=pool,
                    )
                    score = await score_section_quality(
                        screenshot, plan.section_type, tokens,
                    )

                    if score.passed or score.overall >= SECTION_PASS_THRESHOLD:
                        elapsed = time.monotonic() - t0
                        return SectionResult(
                            section_type=plan.section_type,
                            html=current_html,
                            screenshot=screenshot,
                            score=score,
                            iterations=iteration,
                            passed=True,
                            error=None,
                            generation_tokens=total_tokens,
                            generation_cost_usd=total_cost,
                            total_time_s=elapsed,
                        )

                    # Accumulate feedback for next iteration
                    accumulated_feedback.extend(score.issues)

                except ImportError:
                    logger.debug("Renderer/scorer not available; skipping VLM feedback")
                except Exception:
                    logger.warning(
                        "Render/score failed for %s iter %d",
                        plan.section_type, iteration, exc_info=True,
                    )
            else:
                # No pool: skip rendering, mark as passed if validation is clean
                if not validation_issues:
                    elapsed = time.monotonic() - t0
                    return SectionResult(
                        section_type=plan.section_type,
                        html=current_html,
                        screenshot=None,
                        score=None,
                        iterations=iteration,
                        passed=True,
                        error=None,
                        generation_tokens=total_tokens,
                        generation_cost_usd=total_cost,
                        total_time_s=elapsed,
                    )

        except Exception as exc:
            last_error = f"Iteration {iteration} failed: {exc}"
            logger.warning(
                "Section %s iter %d error: %s",
                plan.section_type, iteration, exc, exc_info=True,
            )

    # Exhausted iterations
    elapsed = time.monotonic() - t0
    passed = score is not None and score.overall >= SECTION_PASS_THRESHOLD
    return SectionResult(
        section_type=plan.section_type,
        html=current_html,
        screenshot=screenshot,
        score=score,
        iterations=max_iter,
        passed=passed,
        error=last_error,
        generation_tokens=total_tokens,
        generation_cost_usd=total_cost,
        total_time_s=elapsed,
    )


# ---------------------------------------------------------------------------
# Iteration strategy (Task 2)
# ---------------------------------------------------------------------------


async def _iterate_section(
    current_html: str,
    feedback: SectionScore,
    plan: SectionPlan,
    contract: dict[str, Any],
    tokens: DesignTokens,
    iteration: int,
    tier: BuildTier | None = None,
) -> str:
    """Produce next iteration of section HTML.

    Rounds 1-2 (iteration <= 2): PATCH -- send current HTML + VLM issues.
    Round 3 (iteration == 3): REGENERATE -- fresh generation with accumulated
    feedback as anti-patterns.
    """
    model = "smart"
    if tier is not None:
        model = tier.model

    if iteration <= 2:
        # PATCH: fix specific issues without rewriting
        issues_str = "\n".join(f"- {i}" for i in feedback.issues) if feedback.issues else "None"
        fixes_str = "\n".join(f"- {f}" for f in feedback.actionable_fixes) if feedback.actionable_fixes else "None"

        prompt = textwrap.dedent(f"""\
            Here is a website section that needs improvements:

            ```html
            {current_html}
            ```

            VLM visual analysis found these issues:
            {issues_str}

            Make these specific fixes:
            {fixes_str}

            Rules:
            - Only change what's broken. Preserve everything that works.
            - Keep the same Tailwind classes and design token references.
            - Output ONLY the complete fixed <section>...</section> HTML.
            - No explanations or commentary.
        """)
    else:
        # REGENERATE: fresh generation with accumulated knowledge
        all_issues = list(feedback.issues)
        all_issues.extend(feedback.actionable_fixes)
        issues_str = "\n".join(f"- {i}" for i in all_issues) if all_issues else "None"

        # Load snippet for fresh context
        snippet_html: str | None = None
        if plan.snippet_ref:
            try:
                from clawdbot.snippet_registry import load_snippet
                snip = load_snippet(plan.section_type, plan.snippet_ref)
                snippet_html = snip.get("html")
            except (FileNotFoundError, ImportError):
                pass

        original_prompt = build_section_prompt(
            plan, contract, tokens, snippet=snippet_html,
        )

        prompt = textwrap.dedent(f"""\
            Generate a new {plan.section_type} section from scratch.
            Previous attempts had these recurring issues -- AVOID them:
            {issues_str}

            {original_prompt}
        """)

    return await _call_llm(prompt, model=model)


# ---------------------------------------------------------------------------
# LLM call helpers
# ---------------------------------------------------------------------------


async def _call_llm(prompt: str, *, model: str = "smart") -> str:
    """Call the appropriate LLM backend based on model string.

    - "smart", "fast", "genius" -> shared.llm_client
    - "ollama:*" -> direct Ollama HTTP call
    """
    if model.startswith("ollama:"):
        return await _call_ollama(prompt, model=model.split(":", 1)[1])

    from shared.llm_client import llm
    return await llm.generate(
        prompt,
        model=model,
        max_tokens=4096,
        temperature=0.4,
        pipeline_stage="section_agent_generate",
        operation="clawdbot.section_agent_generate",
        daemon_name="clawdbot",
    )


async def _call_ollama(prompt: str, *, model: str = "qwen2.5-coder:14b") -> str:
    """Call Ollama directly for local code generation models."""
    import httpx

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 4096},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "")


def _estimate_token_cost(model: str, tokens: int) -> float:
    """Estimate cost for a given model and token count."""
    cost_per_1k: dict[str, float] = {
        "fast": 0.001,
        "smart": 0.015,
        "genius": 0.075,
    }
    # Ollama models are free
    if model.startswith("ollama:"):
        return 0.0
    rate = cost_per_1k.get(model, 0.015)
    return (tokens / 1000.0) * rate
