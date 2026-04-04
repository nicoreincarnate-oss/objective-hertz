"""Visual QA gate for generated websites.

Renders a site in browser-use, screenshots at 3 viewports,
sends to Claude vision for design scoring on 7 dimensions.
Gates deployment: score >= 7.0 passes, 5.0-6.9 regenerates with feedback, < 5.0 escalates.
"""

import os
import json
import logging
import base64
import asyncio
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

VIEWPORTS = [
    {"name": "desktop", "width": 1440, "height": 900},
    {"name": "tablet", "width": 768, "height": 1024},
    {"name": "mobile", "width": 375, "height": 812},
]

SCORING_DIMENSIONS = [
    "visual_hierarchy",      # Clear focal point, natural eye flow
    "spacing_alignment",     # Consistent padding, proper grid
    "typography",            # Readable sizes, contrast, font pairing
    "color_harmony",         # Cohesive palette, WCAG AA contrast
    "component_quality",     # Components render correctly, no broken layouts
    "mobile_responsiveness", # Layout adapts properly across viewports
    "professional_polish",   # Would a real business show this to clients?
]

PASS_THRESHOLD = 7.0
REGENERATE_THRESHOLD = 5.0
MAX_REGENERATION_ATTEMPTS = 2


@dataclass
class QAResult:
    """Result of a visual QA evaluation."""
    passed: bool
    average_score: float
    scores: dict[str, float] = field(default_factory=dict)
    feedback: str = ""
    screenshots: dict[str, str] = field(default_factory=dict)  # viewport -> base64 image
    action: str = ""  # "pass", "regenerate", "escalate"
    attempt: int = 0


def _viewport_by_name(name: str) -> dict:
    """Look up a viewport dict by name."""
    for v in VIEWPORTS:
        if v["name"] == name:
            return v
    return {"name": name, "width": 0, "height": 0}


async def capture_screenshots(site_url: str) -> dict[str, bytes]:
    """
    Capture screenshots of a site at 3 viewports using browser-use sidecar.
    Returns dict mapping viewport name to PNG bytes.
    """
    screenshots = {}

    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed for browser-use communication")
        return screenshots

    browser_use_url = os.environ.get("BROWSER_USE_URL", "http://localhost:3031")

    async with httpx.AsyncClient(timeout=60.0) as client:
        for viewport in VIEWPORTS:
            try:
                response = await client.post(
                    f"{browser_use_url}/run",
                    json={
                        "objective": f"Navigate to {site_url} and take a screenshot",
                        "url": site_url,
                        "session_name": f"qa_{viewport['name']}",
                        "viewport": {
                            "width": viewport["width"],
                            "height": viewport["height"],
                        },
                    },
                )

                if response.status_code == 200:
                    result = response.json()
                    # Browser-use returns screenshot as base64 in result
                    if "screenshot" in result:
                        screenshots[viewport["name"]] = base64.b64decode(
                            result["screenshot"]
                        )
                    else:
                        logger.warning(
                            "No screenshot in browser-use response for %s",
                            viewport["name"],
                        )
                else:
                    logger.error(
                        "Browser-use returned %d for %s",
                        response.status_code,
                        viewport["name"],
                    )

            except Exception as e:
                logger.error(
                    "Screenshot capture failed for %s: %s",
                    viewport["name"],
                    e,
                )

    return screenshots


async def score_with_vision(
    screenshots: dict[str, bytes],
) -> tuple[dict[str, float], str]:
    """
    Send screenshots to Claude vision model for design scoring.
    Returns (scores_dict, feedback_text).
    """
    try:
        import anthropic
    except ImportError:
        logger.error("anthropic SDK not installed")
        return {dim: 5.0 for dim in SCORING_DIMENSIONS}, "Vision scoring unavailable"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (
            {dim: 5.0 for dim in SCORING_DIMENSIONS},
            "No API key for vision scoring",
        )

    # Build vision message with all screenshots
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "You are a senior web designer reviewing a generated website. "
                "Score it on each dimension from 1-10."
            ),
        }
    ]

    for viewport_name, png_bytes in screenshots.items():
        vp = _viewport_by_name(viewport_name)
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(png_bytes).decode(),
                },
            }
        )
        content.append(
            {
                "type": "text",
                "text": (
                    f"[{viewport_name} viewport "
                    f"({vp['width']}x{vp['height']})]"
                ),
            }
        )

    scoring_prompt = """
Score this website on each dimension (1-10). Be strict — a 7 means "good enough to show a client."

Respond in this exact JSON format:
{
    "scores": {
        "visual_hierarchy": <1-10>,
        "spacing_alignment": <1-10>,
        "typography": <1-10>,
        "color_harmony": <1-10>,
        "component_quality": <1-10>,
        "mobile_responsiveness": <1-10>,
        "professional_polish": <1-10>
    },
    "feedback": "<Specific, actionable feedback for improving the weakest dimensions. Reference specific sections and viewport issues. Be concrete: 'the hero section heading is too small on mobile' not 'improve typography'.>"
}
"""
    content.append({"type": "text", "text": scoring_prompt})

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",  # Sonnet for cost efficiency
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )

        # Parse JSON response
        response_text = response.content[0].text

        # Extract JSON from response (may be wrapped in markdown code block)
        json_str = response_text
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        result = json.loads(json_str.strip())
        scores = result.get("scores", {})
        feedback = result.get("feedback", "")

        # Validate scores — clamp to [1.0, 10.0]
        validated_scores = {}
        for dim in SCORING_DIMENSIONS:
            score = scores.get(dim, 5.0)
            validated_scores[dim] = max(1.0, min(10.0, float(score)))

        return validated_scores, feedback

    except Exception as e:
        logger.error("Vision scoring failed: %s", e)
        return (
            {dim: 5.0 for dim in SCORING_DIMENSIONS},
            f"Vision scoring error: {e}",
        )


async def evaluate_site(
    site_url: str,
    attempt: int = 0,
) -> QAResult:
    """
    Full visual QA evaluation of a generated site.

    1. Captures screenshots at 3 viewports
    2. Sends to Claude vision for scoring
    3. Returns QAResult with pass/fail/regenerate decision
    """
    # Capture screenshots
    screenshots = await capture_screenshots(site_url)

    if not screenshots:
        logger.error("No screenshots captured — cannot evaluate")
        return QAResult(
            passed=False,
            average_score=0.0,
            action="escalate",
            feedback="Failed to capture screenshots — browser-use may be unavailable",
            attempt=attempt,
        )

    # Score with vision model
    scores, feedback = await score_with_vision(screenshots)

    # Calculate average
    avg_score = sum(scores.values()) / len(scores)

    # Determine action
    if avg_score >= PASS_THRESHOLD:
        action = "pass"
        passed = True
    elif avg_score >= REGENERATE_THRESHOLD and attempt < MAX_REGENERATION_ATTEMPTS:
        action = "regenerate"
        passed = False
    else:
        action = "escalate"
        passed = False

    # Encode screenshots for storage
    screenshot_b64 = {
        name: base64.b64encode(data).decode() for name, data in screenshots.items()
    }

    logger.info(
        "Visual QA: avg=%.1f, action=%s, attempt=%d | scores: %s",
        avg_score,
        action,
        attempt + 1,
        ", ".join(f"{k}={v:.0f}" for k, v in scores.items()),
    )

    return QAResult(
        passed=passed,
        average_score=avg_score,
        scores=scores,
        feedback=feedback,
        screenshots=screenshot_b64,
        action=action,
        attempt=attempt,
    )


async def run_qa_loop(
    site_url: str,
    regenerate_callback=None,
) -> QAResult:
    """
    Run the full QA loop: evaluate -> regenerate (if needed) -> re-evaluate.

    Args:
        site_url: URL of the site to evaluate
        regenerate_callback: async function(feedback: str) -> new_site_url
            Called when the site needs regeneration. Should rebuild the site
            incorporating the vision feedback and return the new URL.

    Returns:
        Final QAResult after all attempts
    """
    result = None
    for attempt in range(MAX_REGENERATION_ATTEMPTS + 1):
        result = await evaluate_site(site_url, attempt=attempt)

        if result.action == "pass":
            logger.info(
                "Visual QA PASSED on attempt %d: %.1f",
                attempt + 1,
                result.average_score,
            )
            return result

        if result.action == "escalate":
            logger.warning(
                "Visual QA ESCALATED on attempt %d: %.1f",
                attempt + 1,
                result.average_score,
            )
            return result

        if result.action == "regenerate" and regenerate_callback:
            logger.info(
                "Visual QA regenerating (attempt %d): %s...",
                attempt + 1,
                result.feedback[:100],
            )
            try:
                new_url = await regenerate_callback(result.feedback)
                if new_url:
                    site_url = new_url
                else:
                    logger.error("Regeneration returned no URL")
                    result.action = "escalate"
                    return result
            except Exception as e:
                logger.error("Regeneration failed: %s", e)
                result.action = "escalate"
                return result
        elif result.action == "regenerate" and not regenerate_callback:
            logger.warning(
                "Regeneration needed but no callback provided — escalating"
            )
            result.action = "escalate"
            return result

    return result  # type: ignore[return-value]
