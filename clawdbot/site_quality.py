"""Shared site-quality evaluation for demos and shipped sites."""

from __future__ import annotations

import json
import re
from typing import Any

from shared.llm_client import llm

PLACEHOLDER_ASSET_PATTERNS = (
    "unsplash.com",
    "source.unsplash",
    "images.unsplash",
    "picsum.photos",
    "via.placeholder",
    "placehold.co",
    "dummyimage.com",
    "placekitten.com",
)

PLACEHOLDER_COPY_PATTERNS = (
    "lorem ipsum",
    "coming soon",
    "your business here",
    "your company",
    "placeholder",
    "sample text",
    "dummy text",
)

ERROR_PATTERNS = (
    "404",
    "500 internal server error",
    "application error",
    "runtime error",
    "something went wrong",
)

MOTION_PATTERNS = (
    "intersectionobserver",
    "gsap",
    "scrolltrigger",
    "requestanimationframe",
    "@keyframes",
    "animation:",
    "transition:",
    "transform:",
    "lenis",
    "<canvas",
    "<svg",
)

PROOF_PATTERNS = (
    "testimonial",
    "review",
    "trusted by",
    "why choose",
    "before and after",
    "case study",
    "customers",
    "projects completed",
    "years of experience",
)

STYLE_PATTERNS = (
    "linear-gradient",
    "radial-gradient",
    "--",
    "backdrop-filter",
    "mix-blend-mode",
    "box-shadow",
    "letter-spacing",
    "font-family",
    "grid-template",
)


def _contains_any(haystack: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in haystack for pattern in patterns)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def analyze_site_markup(html: str, *, business_name: str = "", site_type: str = "demo") -> dict[str, Any]:
    """Score a built site for minimum business-safe quality.

    The goal is not "award-winning" detection; it is to block obviously generic,
    placeholder, or broken output before it reaches a prospect or paid client.
    """
    normalized_type = "full" if site_type == "full" else "demo"
    lower = html.lower()
    business_lower = business_name.lower().strip()
    visible_text = re.sub(r"\s+", " ", _strip_tags(lower)).strip()

    min_html_length = 1500 if normalized_type == "demo" else 2600
    min_text_length = 220 if normalized_type == "demo" else 450
    threshold = 0.62 if normalized_type == "demo" else 0.72

    checks = {
        "has_content": len(html) >= min_html_length or len(visible_text) >= min_text_length,
        "has_business_name": business_lower in lower if business_lower else True,
        "not_error_page": not _contains_any(lower[:1000], ERROR_PATTERNS),
        "no_placeholder_copy": not _contains_any(lower, PLACEHOLDER_COPY_PATTERNS),
        "no_placeholder_assets": not _contains_any(lower, PLACEHOLDER_ASSET_PATTERNS),
        "has_heading": "<h1" in lower,
        "has_navigation": "<nav" in lower or "menu" in lower,
        "has_footer": "<footer" in lower,
        "has_contact_surface": (
            "<form" in lower
            or "mailto:" in lower
            or "tel:" in lower
            or "contact" in lower
            or "book" in lower
            or "quote" in lower
        ),
        "has_proof_signal": _contains_any(lower, PROOF_PATTERNS),
        "has_motion_signal": _contains_any(lower, MOTION_PATTERNS),
        "has_style_signal": _contains_any(lower, STYLE_PATTERNS),
        # SEO checks (added by AEGIS remediation)
        "has_meta_description": '<meta name="description"' in lower,
        "has_og_tags": "og:title" in lower and "og:description" in lower,
        "has_schema_json_ld": "application/ld+json" in lower,
        "has_canonical": 'rel="canonical"' in lower,
        "has_title_tag": "<title>" in lower and "</title>" in lower,
    }

    mandatory = ("has_content", "has_business_name", "not_error_page", "no_placeholder_copy", "no_placeholder_assets")
    weighted = (
        "has_heading",
        "has_navigation",
        "has_footer",
        "has_contact_surface",
        "has_proof_signal",
        "has_motion_signal",
        "has_style_signal",
        # SEO weighted checks — sites without these still pass but score lower
        "has_meta_description",
        "has_og_tags",
        "has_schema_json_ld",
        "has_canonical",
        "has_title_tag",
    )

    mandatory_failures = [name for name in mandatory if not checks[name]]
    weighted_passed = sum(1 for name in weighted if checks[name])
    weighted_score = weighted_passed / len(weighted) if weighted else 0.0
    passed = not mandatory_failures and weighted_score >= threshold

    issues = []
    for name in mandatory_failures:
        issues.append(name)
    for name in weighted:
        if not checks[name]:
            issues.append(name)

    return {
        "passed": passed,
        "site_type": normalized_type,
        "score": round(weighted_score, 3),
        "threshold": threshold,
        "checks": checks,
        "issues": issues,
        "reason": "ok" if passed else ", ".join(issues[:6]) or "quality_gate_failed",
        "metrics": {
            "html_length": len(html),
            "visible_text_length": len(visible_text),
        },
    }


async def _capture_site_screenshots(
    *,
    html: str = "",
    url: str = "",
) -> dict[str, Any]:
    """Capture desktop and mobile screenshots for a site review."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed for visual QA") from exc

    if not html and not url:
        raise ValueError("html or url is required for screenshot capture")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            desktop = await browser.new_page(viewport={"width": 1440, "height": 900})
            mobile = await browser.new_page(
                viewport={"width": 390, "height": 844},
                is_mobile=True,
                device_scale_factor=2,
                has_touch=True,
            )

            if url:
                await desktop.goto(url, wait_until="networkidle", timeout=30000)
                await mobile.goto(url, wait_until="networkidle", timeout=30000)
            else:
                await desktop.set_content(html, wait_until="load")
                await mobile.set_content(html, wait_until="load")
                await desktop.wait_for_timeout(1800)
                await mobile.wait_for_timeout(1800)

            desktop_png = await desktop.screenshot(full_page=False, type="png")
            mobile_png = await mobile.screenshot(full_page=False, type="png")

            return {
                "desktop": desktop_png,
                "mobile": mobile_png,
            }
        finally:
            await browser.close()


async def _visual_critic(
    *,
    screenshots: dict[str, bytes],
    business_name: str = "",
    site_type: str = "demo",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use Claude vision to score the rendered experience."""
    context = context or {}
    prompt = f"""You are a brutal but fair web design critic evaluating a rendered website.

Business name: {business_name or "Unknown business"}
Site type: {site_type}
Build context: {json.dumps(context, ensure_ascii=True)}

Evaluate the screenshots on:
1. layout and hierarchy
2. typography quality
3. motion/interactivity apparent from composition and affordances
4. brand specificity / anti-template feel
5. mobile quality
6. credibility / conversion readiness

Rules:
- Be hard on generic output.
- Penalize placeholder-feeling heroes, weak hierarchy, awkward spacing, bland stock-site composition, and mobile sloppiness.
- Reward bold but coherent composition, clear CTA flow, and business-specific personality.
- If the site looks broken, call it out directly.

Return JSON only:
{{
  "score": 0-100,
  "passed": true,
  "summary": "...",
  "strengths": ["..."],
  "issues": ["..."],
  "revision_instructions": ["..."]
}}

Pass only if the site is genuinely good enough to send to a prospect or client. A merely acceptable generic site should fail.
"""

    result = await llm.generate_with_images(
        prompt,
        images=[screenshots["desktop"], screenshots["mobile"]],
        model="smart",
        max_tokens=1200,
        temperature=0.1,
        pipeline_stage=f"{site_type}_visual_critic",
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
    except Exception as exc:
        raise RuntimeError(f"Could not parse visual critic response: {exc}") from exc

    score = int(parsed.get("score", 0) or 0)
    passed = bool(parsed.get("passed", False))
    return {
        "score": score,
        "passed": passed and score >= (78 if site_type == "full" else 72),
        "summary": parsed.get("summary", ""),
        "strengths": parsed.get("strengths", []),
        "issues": parsed.get("issues", []),
        "revision_instructions": parsed.get("revision_instructions", []),
    }


async def evaluate_multipage_site(
    *,
    pages: dict[str, str],
    business_name: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate all pages of a multi-page site. All must pass mandatory markup checks."""
    if not pages:
        return {"passed": False, "reason": "no_pages_provided", "page_results": {}}

    page_results: dict[str, dict] = {}
    all_issues: list[str] = []
    scores: list[float] = []
    all_passed = True

    for filename, html in pages.items():
        result = analyze_site_markup(html, business_name=business_name, site_type="full")
        page_results[filename] = result
        scores.append(result.get("score", 0.0))
        if not result.get("passed"):
            all_passed = False
            all_issues.append(f"{filename}: {result.get('reason', 'failed')}")
        all_issues.extend(f"{filename}: {issue}" for issue in result.get("issues", []))

    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Visual critique: screenshot index + one inner page
    visual: dict[str, Any] = {"available": False, "reason": "not_attempted"}
    if all_passed and "index.html" in pages:
        try:
            inner_page = next(
                (name for name in ["services.html", "about.html", "contact.html"] if name in pages),
                None,
            )
            screenshots = await _capture_site_screenshots(html=pages["index.html"])
            if inner_page:
                inner_shots = await _capture_site_screenshots(html=pages[inner_page])
                screenshots["inner_desktop"] = inner_shots["desktop"]

            visual = await _visual_critic(
                screenshots=screenshots,
                business_name=business_name,
                site_type="full",
                context={**(context or {}), "multipage": True, "page_count": len(pages)},
            )
            if not visual.get("passed"):
                all_passed = False
                all_issues.extend(visual.get("issues", []))
        except Exception as exc:
            all_passed = False
            visual = {"available": False, "reason": str(exc)[:200]}
            all_issues.append(f"visual_review_unavailable: {str(exc)[:200]}")

    return {
        "passed": all_passed,
        "site_type": "full",
        "score": round(avg_score, 3),
        "page_count": len(pages),
        "page_results": page_results,
        "issues": all_issues,
        "reason": "ok" if all_passed else "; ".join(all_issues[:8]) or "multipage_quality_failed",
        "visual_review": visual,
    }


async def evaluate_site_experience(
    *,
    html: str = "",
    url: str = "",
    business_name: str = "",
    site_type: str = "demo",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run markup heuristics plus screenshot-based visual critique."""
    if not html and not url:
        raise ValueError("html or url is required")

    markup = analyze_site_markup(html or "", business_name=business_name, site_type=site_type) if html else {
        "passed": True,
        "site_type": "full" if site_type == "full" else "demo",
        "score": 1.0,
        "threshold": 0.0,
        "checks": {},
        "issues": [],
        "reason": "ok",
        "metrics": {},
    }
    if not markup.get("passed"):
        return {
            **markup,
            "visual_review": {"available": False, "reason": "blocked_by_markup_checks"},
        }

    try:
        screenshots = await _capture_site_screenshots(html=html, url=url)
        visual = await _visual_critic(
            screenshots=screenshots,
            business_name=business_name,
            site_type=site_type,
            context=context,
        )
    except Exception as exc:
        return {
            **markup,
            "passed": False,
            "reason": f"visual_review_unavailable: {str(exc)[:200]}",
            "issues": list(markup.get("issues", [])) + ["visual_review_unavailable"],
            "visual_review": {
                "available": False,
                "reason": str(exc)[:200],
            },
        }

    combined_issues = list(markup.get("issues", []))
    combined_issues.extend(visual.get("issues", []))
    if not visual.get("passed"):
        combined_issues.extend(visual.get("revision_instructions", []))

    return {
        **markup,
        "passed": bool(markup.get("passed")) and bool(visual.get("passed")),
        "reason": "ok" if markup.get("passed") and visual.get("passed") else "; ".join(combined_issues[:8]) or "visual_critic_failed",
        "issues": combined_issues,
        "visual_review": {
            "available": True,
            **visual,
        },
    }
