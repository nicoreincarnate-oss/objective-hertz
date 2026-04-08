"""
ClawdBot site builder — 5-agent competitive build process.

Instead of one v0.dev call, this runs up to 5 parallel design agents,
reviews them with Opus, cherry-picks the best elements, and deploys
the final version via v0.dev.

Used for both demo sites (close_deal) and full client sites (build_site).
"""

import asyncio
import json
import logging
import os
import re
from datetime import date
from typing import Any

from clawdbot.aider_build import aider_build_enabled, handle_aider_site_build
from clawdbot.design_sources import _extract_research_facts
from clawdbot.site_quality import evaluate_site_experience
from shared.anti_slop import (
    AntiSlopScorer,
    detect_secrets,
    record_quality_score,
    rewrite_loop,
)
from shared.anti_slop import (
    is_enabled as anti_slop_enabled,
)
from shared.comms import record_decision
from shared.db import emit_event
from shared.llm_client import llm

logger = logging.getLogger("perseus.clawdbot.site_builder")

# Anti-slop scorer singleton
_slop_scorer = AntiSlopScorer()


def _extract_text_sections(html: str) -> list[str]:
    """Extract visible text sections from HTML for quality scoring.

    Strips tags, splits by blank lines, returns non-trivial sections (>20 chars).
    """
    import re as _re

    # Remove script and style blocks
    cleaned = _re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"<style[^>]*>[\s\S]*?</style>", "", cleaned, flags=_re.IGNORECASE)
    # Remove HTML tags
    text = _re.sub(r"<[^>]+>", "\n", cleaned)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Split into sections
    sections = []
    for block in text.split("\n\n"):
        block = block.strip()
        if len(block) > 20:
            sections.append(block)
    return sections


async def _anti_slop_site_gate(html: str, lead_id: int | str) -> tuple[str, bool]:
    """Run anti-slop quality gate on site HTML copy.

    Returns (possibly_rewritten_html, passed).
    If anti-slop is disabled, returns original HTML with passed=True.
    If secrets are detected, returns original HTML with passed=False (hard block).
    Scores each text section and rewrites those below threshold.
    """
    if not anti_slop_enabled():
        return html, True

    # Secret detection on full HTML — hard block
    secrets = detect_secrets(html)
    if secrets:
        logger.error(
            "BLOCKED: secrets detected in site HTML for lead %s: %s",
            lead_id,
            [s["pattern"] for s in secrets],
        )
        return html, False

    sections = _extract_text_sections(html)
    if not sections:
        return html, True

    from shared.anti_slop import _composite_score, _is_good_enough

    total_rewrites = 0
    all_scores: list[dict[str, float]] = []

    for section in sections:
        scores = await _slop_scorer.score(section, context="site_copy")
        all_scores.append(scores)

        if not _is_good_enough(scores, "site_copy"):
            rewritten = await rewrite_loop(section, scores, context="site_copy", max_iterations=2)
            if rewritten != section:
                # Replace the section in the HTML (best-effort text replacement)
                html = html.replace(section, rewritten, 1)
                total_rewrites += 1

    # Record aggregate quality score
    if all_scores:
        avg_scores = {
            dim: sum(s.get(dim, 0.0) for s in all_scores) / len(all_scores)
            for dim in ("clarity", "specificity", "authenticity", "value_density", "slop_score")
        }
        await record_quality_score(
            reference_id=str(lead_id),
            content_type="site_copy",
            scores=avg_scores,
            rewrite_count=total_rewrites,
        )
        logger.info(
            "Anti-slop site gate for lead %s: composite=%.2f, slop=%.2f, sections=%d, rewrites=%d",
            lead_id,
            _composite_score(avg_scores),
            avg_scores.get("slop_score", 0),
            len(sections),
            total_rewrites,
        )

    return html, True


# Design directions for parallel agents
DESIGN_DIRECTIONS = [
    {
        "name": "minimal-geometric",
        "style": "Swiss minimalism, mathematical precision, bold negative space",
        "colors": "Near-black + white + single coral accent",
        "fonts": "Satoshi (display) + IBM Plex Sans (body)",
        "layout": "Centered single-column hero, strict grid, maximum whitespace",
        "animation": "Subtle fade-ins only",
        "copy_angle": "Direct, confident, short sentences",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
        ],
        "runtime_hints": [
            "Use GSAP + ScrollTrigger for crisp scroll-driven reveals only where they clarify hierarchy.",
            "Use Lenis for smooth scrolling if it improves the feeling of polish, not as a gimmick.",
            "Prefer excellent easing, spacing, and restraint over lots of animation.",
        ],
        "fallback_rules": [
            "All sections must read clearly with JavaScript disabled.",
            "If animation fails, the hero must still feel intentional through typography and layout alone.",
        ],
    },
    {
        "name": "bold-editorial",
        "style": "Magazine editorial, oversized serif type, asymmetric columns",
        "colors": "Rich navy + warm cream + gold accent",
        "fonts": "Instrument Serif (display) + Source Serif Pro (body)",
        "layout": "Multi-column editorial grid, split hero, pull-quotes",
        "animation": "Text reveals on scroll, parallax on images",
        "copy_angle": "Storytelling, PAS framework",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
        ],
        "runtime_hints": [
            "Use GSAP timeline choreography for type, pull-quote, and image reveal sequences.",
            "Use Lenis to make the editorial flow feel authored and premium.",
            "Motion should reinforce story progression and section transitions.",
        ],
        "fallback_rules": [
            "If JS fails, the editorial hierarchy and CTA sequence must still be obvious.",
            "Do not rely on parallax for readability or meaning.",
        ],
    },
    {
        "name": "dark-cinematic",
        "style": "OLED luxury, cinematic lighting, premium noir",
        "colors": "True black + silver/chrome + electric blue glow",
        "fonts": "Clash Display (display) + Outfit (body)",
        "layout": "Full-bleed dark sections, floating glow cards",
        "animation": "Glow pulses, particles, cursor effects",
        "copy_angle": "Bold, aspirational, power words",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
            "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
        ],
        "runtime_hints": [
            "Use GSAP for hero entrance, cards, and section pacing.",
            "Use PixiJS only for subtle cinematic particle, grain, or glow layers if they materially improve the mood.",
            "Make the page feel expensive, controlled, and dramatic rather than noisy.",
        ],
        "fallback_rules": [
            "If canvas effects fail, fall back to static gradients, layered shadows, and typography.",
            "Never make core navigation or CTA depend on PixiJS.",
        ],
    },
    {
        "name": "organic-illustrated",
        "style": "Biomorphic, hand-crafted, natural textures, warm",
        "colors": "Warm sand + forest green + terracotta accent",
        "fonts": "Fraunces (display) + Work Sans (body)",
        "layout": "Flowing sections with organic wave dividers, rounded cards",
        "animation": "Gentle fades, SVG path drawing",
        "copy_angle": "Warm, conversational, rhetorical questions",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
        ],
        "runtime_hints": [
            "Use GSAP sparingly for SVG path drawing, card reveals, or gentle entrance timing.",
            "Lean on SVG, gradients, and illustration-like composition more than heavy JS.",
            "Keep the emotional tone warm, human, and handcrafted.",
        ],
        "fallback_rules": [
            "All decorative motion must degrade cleanly to static SVG or CSS.",
            "Readable content and trust signals come before ornament.",
        ],
    },
    {
        "name": "playful-animated",
        "style": "Motion-forward, vibrant energy, bold color blocks",
        "colors": "Indigo + hot pink + lime accent on white",
        "fonts": "Cabinet Grotesk (display) + Plus Jakarta Sans (body)",
        "layout": "Bento grid layout, diagonal section breaks",
        "animation": "Bouncy springs, hover scale, count-ups, gradient mesh",
        "copy_angle": "Energetic, fun, analogies",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
            "https://cdn.jsdelivr.net/npm/tone@15.0.4/build/Tone.js",
        ],
        "runtime_hints": [
            "Use GSAP for playful, elastic transitions and hover choreography.",
            "Use PixiJS only if a lightweight hero or background canvas creates delight without hurting clarity.",
            "Use Tone.js only for opt-in or click-triggered sound interactions; never autoplay sound.",
        ],
        "fallback_rules": [
            "If JS fails, the site must still feel fun through color, shape, and typography.",
            "If audio or canvas is used, provide a silent/static fallback immediately.",
        ],
    },
    {
        "name": "immersive-3d",
        "style": "Spatial, sculptural, cinematic object-first storytelling",
        "colors": "Moody charcoal + soft ivory + one acid accent",
        "fonts": "General Sans (display) + Inter (body)",
        "layout": "Hero anchored by a 3D object or spatial scene with layered supporting content",
        "animation": "Camera-driven reveals, depth, parallax, sculptural motion",
        "copy_angle": "Confident and world-building without losing clarity",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
        ],
        "runtime_hints": [
            "Use Three.js only if it earns the concept, ideally in the hero or one signature section.",
            "Use ScrollTrigger to drive camera, object motion, or reveal choreography.",
            "Keep the 3D scene simple and performant enough to load from a CDN-only build.",
        ],
        "fallback_rules": [
            "Provide a static hero composition or poster frame if WebGL is unavailable.",
            "Never make important business information depend on Three.js rendering.",
        ],
    },
    {
        "name": "audio-reactive-canvas",
        "style": "Instrument-like, rhythmic, visualized sound and energy",
        "colors": "Near-black + neon cyan + warm amber",
        "fonts": "Space Grotesk (display) + Inter (body)",
        "layout": "Focused hero with one playable or reactive signature interaction",
        "animation": "Reactive waveforms, pulses, and visualized timing",
        "copy_angle": "Lean, precise, and sensory",
        "cdn_deps": [
            "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
            "https://cdn.jsdelivr.net/npm/tone@15.0.4/build/Tone.js",
            "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
        ],
        "runtime_hints": [
            "Use PixiJS for the visual surface and Tone.js for click-triggered or opt-in reactive audio.",
            "Do not autoplay sound; require a clear user gesture.",
            "The page should feel like one memorable instrument-like moment, not a toy box.",
        ],
        "fallback_rules": [
            "If audio is unavailable, fall back to the visual system only.",
            "If canvas fails, show a strong static hero with the same emotional direction.",
        ],
    },
]


def _normalize_authored_entries(value: Any) -> list[dict[str, str]]:
    """Normalize authored asset references from strings/lists/dicts into prompt-safe dicts."""
    entries: list[dict[str, str]] = []

    def add(item: Any) -> None:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                entries.append({"url": cleaned, "usage": "", "notes": ""})
        elif isinstance(item, dict):
            url = str(item.get("url", "") or item.get("src", "") or item.get("href", "")).strip()
            usage = str(item.get("usage", "") or item.get("role", "") or item.get("placement", "")).strip()
            notes = str(item.get("notes", "") or item.get("description", "") or item.get("prompt", "")).strip()
            name = str(item.get("name", "") or item.get("title", "")).strip()
            if url or usage or notes or name:
                entry = {"url": url, "usage": usage, "notes": notes}
                if name:
                    entry["name"] = name
                entries.append(entry)

    if isinstance(value, list):
        for item in value:
            add(item)
    else:
        add(value)

    return entries


def _resolve_authored_assets(lead: dict, strategy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve optional authored assets that the builder may embed if available."""
    strategy = strategy or {}

    def first_non_empty(*keys: str) -> Any:
        for key in keys:
            if key in lead and lead.get(key):
                return lead.get(key)
            if key in strategy and strategy.get(key):
                return strategy.get(key)
        return None

    rive_assets = _normalize_authored_entries(
        first_non_empty("rive_assets", "rive_urls", "rive_asset", "rive_url")
    )
    spline_assets = _normalize_authored_entries(
        first_non_empty("spline_assets", "spline_scenes", "spline_urls", "spline_scene", "spline_url")
    )
    theatre_sequences = _normalize_authored_entries(
        first_non_empty("theatre_sequences", "theatre_sequence", "theatre_notes", "theatre_js_notes")
    )

    return {
        "rive_assets": rive_assets,
        "spline_assets": spline_assets,
        "theatre_sequences": theatre_sequences,
        "has_authored_assets": bool(rive_assets or spline_assets or theatre_sequences),
    }


def _format_authored_asset_guidance(asset_pack: dict[str, Any]) -> str:
    """Render optional authored assets into a prompt block."""
    rive_assets = asset_pack.get("rive_assets", []) or []
    spline_assets = asset_pack.get("spline_assets", []) or []
    theatre_sequences = asset_pack.get("theatre_sequences", []) or []

    if not rive_assets and not spline_assets and not theatre_sequences:
        return """AUTHORED ASSETS:
- No authored Rive, Spline, or Theatre.js inputs are available for this build.
- Do not invent or fake those assets.
- If they are absent, rely on HTML/CSS/SVG/Canvas composition instead."""

    lines = ["AUTHORED ASSETS AVAILABLE:"]

    if rive_assets:
        lines.append("- Rive assets (optional; use for icon, hero accent, or microinteraction only):")
        for item in rive_assets[:3]:
            lines.append(
                f"  - URL: {item.get('url', '') or 'n/a'} | usage: {item.get('usage', '') or 'microinteraction'} | notes: {item.get('notes', '') or 'keep it lightweight'}"
            )
    else:
        lines.append("- No Rive assets available. Do not fake .riv usage.")

    if spline_assets:
        lines.append("- Spline assets (optional; use for a hero object or one spatial scene only):")
        for item in spline_assets[:3]:
            lines.append(
                f"  - URL: {item.get('url', '') or 'n/a'} | usage: {item.get('usage', '') or 'hero scene'} | notes: {item.get('notes', '') or 'keep it focused'}"
            )
    else:
        lines.append("- No Spline scenes available. Do not pretend a Spline asset exists.")

    if theatre_sequences:
        lines.append("- Theatre.js sequence notes (optional choreography input, not mandatory runtime):")
        for item in theatre_sequences[:3]:
            label = item.get("name", "") or item.get("usage", "") or "sequence"
            lines.append(
                f"  - {label}: {item.get('notes', '') or item.get('url', '') or 'Use as timing/choreography direction only.'}"
            )
    else:
        lines.append("- No Theatre.js sequence notes available.")

    lines.extend(
        [
            "AUTHORED ASSET RULES:",
            "- Use authored assets only if they clearly strengthen the concept.",
            "- If an authored asset is missing, brittle, or slows the page down, skip it and fall back gracefully.",
            "- Never make core business info, CTA, or navigation depend on authored assets loading.",
            "- If Theatre.js notes are present but a direct Theatre runtime is overkill, translate the timing into GSAP/Three behavior instead.",
        ]
    )
    return "\n".join(lines)


def _site_build_settings() -> Any:
    """Return site-build config with safe defaults for tests/fallbacks."""
    try:
        from shared.config import config as shared_config

        return getattr(shared_config, "site_build", None) or getattr(shared_config, "config", None)
    except Exception:
        return None


async def _safe_fetch_val(query: str, params: tuple = ()) -> Any:
    try:
        from shared.db import fetch_val
    except Exception:
        return None

    try:
        return await fetch_val(query, params)
    except Exception:
        return None


def _infer_build_mode(lead: dict, strategy: dict[str, Any], site_type: str) -> str:
    context = " ".join(
        [
            str(lead.get("industry", "")),
            str(lead.get("business_name", "")),
            str(lead.get("research_summary", "")),
            json.dumps(strategy.get("reference_patterns", [])),
            json.dumps(strategy.get("design_sources", [])),
        ]
    ).lower()

    if any(keyword in context for keyword in ("music", "album", "audio", "sound", "dj")):
        return "audio-reactive"
    if any(keyword in context for keyword in ("museum", "archive", "retro", "nostalgia", "y2k", "collector")):
        return "retro-interface"
    if any(keyword in context for keyword in ("studio", "portfolio", "creative", "design", "agency", "editorial")):
        return "editorial-motion"
    if any(keyword in context for keyword in ("luxury", "furniture", "interior", "architecture", "gallery", "product")):
        return "surreal-product"
    if site_type == "full":
        return "premium-conversion"
    return "conversion"


def _select_runtime_profile(build_mode: str, site_type: str) -> str:
    if build_mode == "audio-reactive":
        return "tone-pixi-lite" if site_type == "demo" else "tone-pixi"
    if build_mode in {"editorial-motion", "surreal-product"}:
        return "gsap-lenis"
    if build_mode == "retro-interface":
        return "retro-dom-motion"
    return "dom-motion"


def _runtime_bundle(build_mode: str, runtime_profile: str) -> dict[str, list[str]]:
    """Return generic runtime guidance for the final synthesized build."""
    if runtime_profile == "gsap-lenis":
        return {
            "cdn_deps": [
                "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
                "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
                "https://cdn.jsdelivr.net/npm/lenis@1.1.14/dist/lenis.min.js",
            ],
            "runtime_hints": [
                "Use GSAP + ScrollTrigger for precise reveal timing and narrative pacing.",
                "Use Lenis only if it improves the smoothness of the authored scroll experience.",
            ],
            "fallback_rules": [
                "Respect prefers-reduced-motion and disable scroll-linked motion when requested.",
                "The hero and CTA must still work if all JS fails.",
            ],
        }
    if runtime_profile == "tone-pixi":
        return {
            "cdn_deps": [
                "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
                "https://cdn.jsdelivr.net/npm/tone@15.0.4/build/Tone.js",
                "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
            ],
            "runtime_hints": [
                "Use PixiJS for a single memorable interactive visual surface.",
                "Use Tone.js only behind an explicit user gesture; never autoplay sound.",
            ],
            "fallback_rules": [
                "If canvas or audio fails, fall back to a static hero and silent experience immediately.",
                "Do not hide navigation, proof, or CTA inside the immersive layer.",
            ],
        }
    if runtime_profile == "tone-pixi-lite":
        return {
            "cdn_deps": [
                "https://cdn.jsdelivr.net/npm/pixi.js@8.2.6/dist/pixi.min.js",
                "https://cdn.jsdelivr.net/npm/tone@15.0.4/build/Tone.js",
            ],
            "runtime_hints": [
                "Keep the interactive layer lightweight and optional.",
                "Use sound only after a user click and only if it clearly improves the concept.",
            ],
            "fallback_rules": [
                "Provide a strong static design if JS, audio, or canvas fails.",
            ],
        }
    if build_mode == "surreal-product":
        return {
            "cdn_deps": [
                "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js",
                "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js",
                "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js",
            ],
            "runtime_hints": [
                "If 3D earns the concept, use a restrained Three.js object or scene in the hero only.",
                "Use GSAP for camera or object reveal choreography rather than constant motion.",
            ],
            "fallback_rules": [
                "Show a static poster-like hero if WebGL is unavailable.",
                "Never make important content depend on 3D rendering.",
            ],
        }
    return {
        "cdn_deps": [],
        "runtime_hints": [
            "Use CSS and light JavaScript only when they materially improve the page.",
        ],
        "fallback_rules": [
            "All content and CTA paths must work without JavaScript.",
        ],
    }


async def _resolve_build_plan(
    lead: dict,
    *,
    site_type: str,
    page_count: int,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    settings = _site_build_settings()
    build_mode = strategy.get("build_mode") or _infer_build_mode(lead, strategy, site_type)
    runtime_profile = strategy.get("runtime_profile") or _select_runtime_profile(build_mode, site_type)
    monthly_paid_asset_cap = float(getattr(settings, "monthly_paid_asset_cap", 30.0) or 30.0)
    per_site_asset_cap = float(
        getattr(settings, "per_full_asset_cap", 0.08) if site_type == "full" else getattr(settings, "per_demo_asset_cap", 0.03)
    )
    max_assets = int(getattr(settings, "max_full_assets", 4) if site_type == "full" else getattr(settings, "max_demo_assets", 2))
    variant_limit = int(getattr(settings, "full_variants", 5) if site_type == "full" else getattr(settings, "demo_variants", 3))

    month_start = date.today().replace(day=1)
    monthly_spend = float(
        await _safe_fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM budget_tracking
               WHERE month = %s AND category = 'recraft_api'""",
            (month_start,),
        )
        or 0.0
    )
    current_client_spend = float(
        await _safe_fetch_val(
            """SELECT COALESCE(SUM(amount), 0) FROM budget_tracking
               WHERE month = %s AND category = 'recraft_api' AND client_id = %s""",
            (month_start, lead.get("id")),
        )
        or 0.0
    )
    remaining_monthly = max(0.0, monthly_paid_asset_cap - monthly_spend)
    remaining_site = max(0.0, per_site_asset_cap - current_client_spend)
    allow_paid_assets = remaining_monthly >= 0.01 and remaining_site >= 0.01 and max_assets > 0
    runtime_bundle = _runtime_bundle(build_mode, runtime_profile)

    return {
        "site_type": site_type,
        "page_count": page_count,
        "build_mode": build_mode,
        "runtime_profile": runtime_profile,
        "variant_limit": max(1, variant_limit),
        "max_assets": max(0, max_assets),
        "allow_paid_assets": allow_paid_assets,
        "remaining_monthly_asset_budget": round(remaining_monthly, 2),
        "remaining_site_asset_budget": round(remaining_site, 2),
        "governing_idea": strategy.get("governing_idea", strategy.get("visual_direction", "Memorable, business-specific conversion design")),
        "emotional_target": strategy.get("emotional_target", "Trust and intrigue in the first 5 seconds"),
        "signature_moments": strategy.get("signature_moments", []),
        "cdn_deps": runtime_bundle["cdn_deps"],
        "runtime_hints": runtime_bundle["runtime_hints"],
        "fallback_rules": runtime_bundle["fallback_rules"],
        "design_sources": strategy.get("design_sources", []),
    }


async def _generate_asset_pack(lead: dict, build_plan: dict[str, Any]) -> dict[str, Any]:
    """Generate only the paid assets the site can justify and afford."""
    if not build_plan.get("allow_paid_assets"):
        return {
            "logo_url": "",
            "hero_url": "",
            "assets_generated": 0,
            "reason": "budget_guard",
        }

    from clawdbot.daemon import handle_image_generation

    business_name = lead.get("business_name", "Business")
    industry = lead.get("industry", "")
    city = lead.get("city", "")
    client_id = lead.get("id")
    generated = 0
    max_assets = int(build_plan.get("max_assets", 0))
    asset_pack = {
        "logo_url": "",
        "hero_url": "",
        "assets_generated": 0,
        "reason": "generated",
    }

    if generated < max_assets:
        logo = await handle_image_generation(
            {
                "type": "logo",
                "business_name": business_name,
                "industry": industry,
                "client_id": client_id,
            }
        )
        if logo.get("url"):
            asset_pack["logo_url"] = logo["url"]
            generated += 1

    should_generate_hero = (
        generated < max_assets
        and build_plan.get("remaining_site_asset_budget", 0.0) >= 0.02
        and (
            build_plan.get("site_type") == "full"
            or build_plan.get("build_mode") in {"editorial-motion", "surreal-product", "audio-reactive"}
        )
    )
    if should_generate_hero:
        hero = await handle_image_generation(
            {
                "type": "hero",
                "business_name": business_name,
                "industry": industry,
                "city": city,
                "client_id": client_id,
            }
        )
        if hero.get("url"):
            asset_pack["hero_url"] = hero["url"]
            generated += 1

    asset_pack["assets_generated"] = generated
    return asset_pack


INNER_PAGE_SPECS = {
    "about.html": {
        "title": "About",
        "description": "Business story, team/founder background, values, years of experience, mission.",
        "data_keys": ["research_summary", "research_facts"],
    },
    "services.html": {
        "title": "Services",
        "description": "Detailed service descriptions, specialties, process/approach, service areas.",
        "data_keys": ["research_facts", "industry"],
    },
    "gallery.html": {
        "title": "Gallery",
        "description": "Portfolio/project showcase using a CSS grid. Use gradients, SVG, and layout to represent work — never stock images.",
        "data_keys": ["research_facts", "industry"],
    },
    "contact.html": {
        "title": "Contact",
        "description": "Contact form, phone, email, business address, hours of operation, map placeholder.",
        "data_keys": ["email", "city", "country", "contact_name"],
    },
}

FULL_SITE_NAV_PAGES = ["index.html", "about.html", "services.html", "gallery.html", "contact.html"]


def _extract_shared_shell(index_html: str) -> str:
    """Extract the reusable design shell (head, nav, footer, styles) from an approved index.html.

    This gives inner-page prompts the exact design system to match, so the LLM
    only needs to generate the <main> content.
    """
    parts = []

    head_match = re.search(r"<head[\s>].*?</head>", index_html, re.DOTALL | re.IGNORECASE)
    if head_match:
        parts.append(f"<!-- SHARED HEAD -->\n{head_match.group()}")

    nav_match = re.search(r"<nav[\s>].*?</nav>", index_html, re.DOTALL | re.IGNORECASE)
    if nav_match:
        parts.append(f"<!-- SHARED NAV -->\n{nav_match.group()}")

    # Also grab <header> if it wraps the nav
    header_match = re.search(r"<header[\s>].*?</header>", index_html, re.DOTALL | re.IGNORECASE)
    if header_match and nav_match and nav_match.group() in header_match.group():
        parts = [p for p in parts if "SHARED NAV" not in p]
        parts.append(f"<!-- SHARED HEADER -->\n{header_match.group()}")

    footer_match = re.search(r"<footer[\s>].*?</footer>", index_html, re.DOTALL | re.IGNORECASE)
    if footer_match:
        parts.append(f"<!-- SHARED FOOTER -->\n{footer_match.group()}")

    # Standalone <style> blocks not inside <head>
    style_blocks = re.findall(r"<style[\s>].*?</style>", index_html, re.DOTALL | re.IGNORECASE)
    for block in style_blocks:
        if head_match and block in head_match.group():
            continue
        parts.append(f"<!-- SHARED STYLE -->\n{block}")

    return "\n\n".join(parts) if parts else ""


def _build_page_brief(page_name: str, lead: dict) -> str:
    """Build a content brief for an inner page using lead data."""
    spec = INNER_PAGE_SPECS.get(page_name, {})
    title: str = str(spec.get("title", page_name.replace(".html", "").title()))
    description = spec.get("description", f"Content for the {title} page.")

    context_parts = []
    for key in spec.get("data_keys", []):
        value = lead.get(key)
        if value:
            if isinstance(value, dict):
                context_parts.append(f"{key}: {json.dumps(value, default=str)[:800]}")
            elif isinstance(value, str):
                context_parts.append(f"{key}: {value[:600]}")
            else:
                context_parts.append(f"{key}: {value}")

    business_name = lead.get("business_name", "Business")
    industry = lead.get("industry", "")
    city = lead.get("city", "")

    return f"""PAGE: {title} ({page_name})
Business: {business_name} ({industry}{f', {city}' if city else ''})
Purpose: {description}

Available business context:
{chr(10).join(context_parts) if context_parts else f'Use general {industry} industry context to write compelling {title.lower()} content.'}

Write real, specific content for this business — never placeholder text."""


async def _build_single_page(
    page_name: str,
    page_brief: str,
    shared_shell: str,
    build_plan: dict[str, Any],
) -> str:
    """Generate a single inner page using the shared design shell from index.html."""
    nav_links = "\n".join(
        f"- {name.replace('.html', '').replace('index', 'Home').title()} → {name}"
        for name in FULL_SITE_NAV_PAGES
    )

    prompt = f"""Build the complete HTML for an inner page of a multi-page website.

{page_brief}

DESIGN SYSTEM (use this EXACTLY — same head, nav, footer, fonts, colors, styles):
{shared_shell}

REQUIREMENTS:
- Start with <!DOCTYPE html> and end with </html>.
- Use the EXACT same <head>, <nav>/<header>, and <footer> from the design system above.
- Only change the <main> content — everything else must match the home page.
- Navigation links (mark {page_name} as the active/current page):
{nav_links}
- Use Tailwind CSS classes consistent with the home page.
- Mobile responsive, accessible (WCAG 2.1 AA).
- Never use placeholder text, lorem ipsum, or stock image URLs.
- If no real images are available, use gradients, SVG patterns, or strong typography instead.

SEO REQUIREMENTS (mandatory):
- Update <title> tag for this specific page (under 60 chars)
- Update <meta name="description"> for this page's content (under 155 chars)
- Update <link rel="canonical"> to this page's URL
- Update Open Graph og:title, og:description, og:url for this page
- Update Twitter twitter:title, twitter:description for this page
- Add page-appropriate Schema.org JSON-LD (AboutPage, Service, CollectionPage, ContactPage)
- Maintain semantic heading hierarchy (one <h1>, then <h2>, <h3>)

Output ONLY the complete HTML. No explanation."""

    result = await llm.generate(
        prompt,
        model="smart",
        max_tokens=6000,
        temperature=0.5,
        pipeline_stage=f"site_build_{page_name}",
    )

    html = _extract_html(result)
    if not html:
        return ""

    # Quick quality check — retry once if it fails basic markup
    from clawdbot.site_quality import analyze_site_markup
    check = analyze_site_markup(html, business_name="", site_type="full")
    if not check.get("passed"):
        logger.info("Inner page %s failed first QA check, retrying: %s", page_name, check.get("reason", ""))
        retry_prompt = f"{prompt}\n\nPREVIOUS ATTEMPT FAILED QA: {check.get('reason', 'quality too low')}. Fix these issues."
        retry_result = await llm.generate(
            retry_prompt,
            model="smart",
            max_tokens=6000,
            temperature=0.4,
            pipeline_stage=f"site_build_{page_name}_retry",
        )
        retry_html = _extract_html(retry_result)
        if retry_html:
            html = retry_html

    return html


async def _generate_additional_pages(
    index_html: str,
    lead: dict,
    brief: str,
    build_plan: dict[str, Any],
) -> dict[str, str]:
    """Generate inner pages for a multi-page full site.

    Extracts the shared design shell from the approved index.html, then generates
    each inner page sequentially so they all share the same nav, footer, and design system.
    """
    shared_shell = _extract_shared_shell(index_html)
    if not shared_shell:
        logger.warning("Could not extract shared shell from index.html — inner pages may be inconsistent")
        # Fall back to using the full index as context (truncated)
        shared_shell = index_html[:6000]

    page_count = build_plan.get("page_count", 5)
    page_names = list(INNER_PAGE_SPECS.keys())[:page_count - 1]

    pages: dict[str, str] = {"index.html": index_html}

    for page_name in page_names:
        try:
            page_brief = _build_page_brief(page_name, lead)
            html = await _build_single_page(page_name, page_brief, shared_shell, build_plan)

            if html:
                pages[page_name] = html
                logger.info("Generated %s (%d chars)", page_name, len(html))
            else:
                # Minimal fallback — shared shell with a heading
                title = str(INNER_PAGE_SPECS[page_name]["title"])
                pages[page_name] = _minimal_fallback_page(shared_shell, title, lead)
                logger.warning("Using minimal fallback for %s", page_name)

        except Exception as e:
            logger.warning("Failed to generate %s: %s — using fallback", page_name, e)
            title = str(INNER_PAGE_SPECS[page_name]["title"])
            pages[page_name] = _minimal_fallback_page(shared_shell, title, lead)

    await emit_event("multipage_generation_completed", {
        "client_id": lead.get("id"),
        "business_name": lead.get("business_name"),
        "pages_generated": len(pages),
        "page_names": list(pages.keys()),
    })

    return pages


def _minimal_fallback_page(shared_shell: str, title: str, lead: dict) -> str:
    """Generate a minimal page using the shared shell when LLM generation fails."""
    business_name = lead.get("business_name", "Business")
    head = re.search(r"<head[\s>].*?</head>", shared_shell, re.DOTALL | re.IGNORECASE)
    nav = re.search(r"<(?:header|nav)[\s>].*?</(?:header|nav)>", shared_shell, re.DOTALL | re.IGNORECASE)
    footer = re.search(r"<footer[\s>].*?</footer>", shared_shell, re.DOTALL | re.IGNORECASE)

    return f"""<!DOCTYPE html>
<html lang="en">
{head.group() if head else '<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>' + title + ' - ' + business_name + '</title><script src="https://cdn.tailwindcss.com"></script></head>'}
<body class="bg-white text-gray-900">
{nav.group() if nav else ''}
<main class="max-w-4xl mx-auto px-6 py-20">
  <h1 class="text-4xl font-bold mb-6">{title}</h1>
  <p class="text-lg text-gray-600">Learn more about {business_name}.</p>
</main>
{footer.group() if footer else ''}
</body>
</html>"""


async def build_demo_site(lead: dict) -> str:
    """Build a demo landing page for a prospect. Returns deployed URL or empty string."""
    return await _build_site(lead, site_type="demo", page_count=1)


async def build_full_site(lead: dict) -> str:
    """Build a full 5-page website for a closed deal. Returns deployed URL or empty string."""
    return await _build_site(lead, site_type="full", page_count=5)


async def _build_site(
    lead: dict, *, site_type: str, page_count: int, _v1_fallback: bool = False,
) -> str:
    """Core build — routes to Aider (opt-in) -> v2 -> v1 based on feature flags.

    Phase 3 Wave 3: when ``ENABLE_AIDER_LOOPS=true`` the opt-in Aider loop runs
    FIRST. If it returns ``skipped`` / ``escalated`` / ``failed`` we fall through
    to the existing competitive build paths so the default behaviour is unchanged.

    When ``CLAWDBOT_V2_ENABLED`` is set the multi-agent visual production
    pipeline is used.  Otherwise the original 5-agent competitive process runs.
    ``_v1_fallback`` is an internal guard that prevents v2 from recursing back
    into itself when it falls back.
    """
    if not _v1_fallback and aider_build_enabled():
        try:
            from pathlib import Path

            site_spec = {
                "brief": lead.get("brief") or lead.get("business_name", ""),
                "brand": {
                    "name": lead.get("business_name", ""),
                    "industry": lead.get("industry", ""),
                },
                "sections": [],
            }
            aider_result = await handle_aider_site_build(
                site_spec,
                llm_client=llm,
                workspace=Path("/tmp/clawdbot_aider"),
            )
            status = aider_result.get("status")
            if status == "complete" and aider_result.get("html"):
                logger.info(
                    "Clawdbot Aider build complete (visual=%.2f, iterations=%d); "
                    "falling through to competitive deploy path",
                    aider_result.get("visual_score", 0.0),
                    aider_result.get("iterations", 0),
                )
                # Aider returns raw HTML; existing v1/v2 paths return a deployed
                # URL. Until a deploy adapter is wired we fall through so
                # customers always get a deployed site.
            else:
                logger.info(
                    "Clawdbot Aider path returned status=%s; falling through",
                    status,
                )
        except Exception as exc:  # noqa: BLE001 -- never break the default path
            logger.warning("Clawdbot Aider hook errored, falling through: %s", exc)

    if (
        not _v1_fallback
        and os.environ.get("CLAWDBOT_V2_ENABLED", "").lower() in ("true", "1", "yes")
    ):
        return await _build_site_v2(lead, site_type=site_type, page_count=page_count)
    return await _build_site_v1(lead, site_type=site_type, page_count=page_count)


async def _build_site_v2(lead: dict, *, site_type: str, page_count: int) -> str:
    """Multi-agent visual production pipeline (phases 33-38).

    Orchestrates: section planning -> parallel section build -> assembly -> QA -> deploy.
    Falls back to v1 on any unrecoverable error.
    """
    business_name = lead.get("business_name", "Business")

    try:
        # 1. Generate build plan
        from clawdbot.section_planner import generate_build_plan

        plan = await generate_build_plan(lead, site_type=site_type, page_count=page_count)

        try:
            await emit_event("site_build_v2_started", {
                "client_id": lead.get("id"),
                "business_name": business_name,
                "sections_planned": plan.total_sections,
                "direction": plan.direction_name,
                "tier": plan.tier.name,
            })
        except Exception:
            pass  # event emission is best-effort

        # 2. Validate design contract (render test hero, VLM check)
        pool = _get_playwright_pool()

        if pool:
            try:
                from clawdbot.visual_scorer import validate_design_contract

                validation = await validate_design_contract(plan.design_tokens, pool)
                if not validation.passed:
                    logger.warning("Design contract validation failed: %s", validation.issues)
            except Exception as exc:
                logger.warning("Design contract validation skipped: %s", exc)

        # 3. Build all sections in parallel
        from clawdbot.section_orchestrator import build_all_sections

        build_result = await build_all_sections(plan, pool=pool)

        # 4. Assemble page(s)
        from clawdbot.page_assembler import assemble_page, assemble_multipage_site

        final_html = await assemble_page(build_result.sections, plan)

        if not final_html:
            raise RuntimeError("Assembly produced empty HTML")

        # 5. Full-page QA
        from clawdbot.fullpage_qa import run_full_page_qa, deploy_gate

        qa_result = await run_full_page_qa(final_html, plan, pool=pool)

        # 6. Iterate if needed
        if not qa_result.passed and pool:
            try:
                from clawdbot.fullpage_qa import iterate_full_page

                final_html, qa_result = await iterate_full_page(
                    final_html, qa_result, plan, build_result.sections, pool=pool,
                )
            except Exception as exc:
                logger.warning("QA iteration failed, proceeding with current HTML: %s", exc)

        # 7. Deploy gate
        should_deploy, reason = await deploy_gate(qa_result)
        if not should_deploy:
            logger.error("Deploy blocked: %s", reason)
            return ""

        # 8. Deploy
        if page_count > 1:
            pages = await assemble_multipage_site(build_result.sections, plan)
            try:
                from clawdbot.deploy import deploy_static_site

                url = await deploy_static_site(pages, business_name, client_id=lead.get("id"))
            except ImportError:
                logger.warning("deploy module unavailable, using v0 fallback for multipage")
                url = await _deploy_to_v0(final_html, business_name, site_type)
        else:
            url = await _deploy_to_v0(final_html, business_name, site_type)

        # 9. Emit completion
        try:
            await emit_event("site_build_v2_completed", {
                "client_id": lead.get("id"),
                "url": url,
                "sections_built": len(build_result.sections),
                "qa_score": qa_result.overall_score,
                "build_time_s": build_result.total_time_s,
                "build_cost_usd": build_result.total_cost_usd,
                "tier": plan.tier.name,
                "direction": plan.direction_name,
            })
        except Exception:
            pass  # event emission is best-effort

        return url

    except Exception as e:
        logger.error("V2 pipeline failed for %s: %s — falling back to v1", business_name, e)
        try:
            await emit_event("site_build_v2_fallback", {
                "client_id": lead.get("id"),
                "error": str(e)[:500],
            })
        except Exception:
            pass
        return await _build_site(
            lead, site_type=site_type, page_count=page_count, _v1_fallback=True,
        )


# ── Playwright pool accessor (set by daemon.py) ──────────────────────
_playwright_pool = None


def _set_playwright_pool(pool: object) -> None:
    """Called by daemon.py to inject the shared Playwright pool."""
    global _playwright_pool
    _playwright_pool = pool


def _get_playwright_pool():
    """Return the daemon-managed Playwright pool, or None."""
    return _playwright_pool


async def _build_site_v1(lead: dict, *, site_type: str, page_count: int) -> str:
    """Original v1 build process: generate variants -> review -> synthesize -> deploy."""
    business_name = lead.get("business_name", "Business")
    _industry = lead.get("industry", "general services")

    strategy = await _generate_reference_strategy(lead, site_type, page_count)
    build_plan = await _resolve_build_plan(lead, site_type=site_type, page_count=page_count, strategy=strategy)
    brief = await _build_product_brief(lead, site_type, page_count, strategy=strategy, build_plan=build_plan)
    asset_pack = await _generate_asset_pack(lead, build_plan)
    asset_pack.update(_resolve_authored_assets(lead, strategy))

    await emit_event(
        "site_build_plan",
        {
            "client_id": lead.get("id"),
            "business_name": business_name,
            "site_type": site_type,
            "build_mode": build_plan.get("build_mode"),
            "runtime_profile": build_plan.get("runtime_profile"),
            "variant_limit": build_plan.get("variant_limit"),
            "allow_paid_assets": build_plan.get("allow_paid_assets"),
            "assets_generated": asset_pack.get("assets_generated", 0),
            "authored_asset_tools": [
                tool
                for tool, available in (
                    ("rive", bool(asset_pack.get("rive_assets"))),
                    ("spline", bool(asset_pack.get("spline_assets"))),
                    ("theatrejs", bool(asset_pack.get("theatre_sequences"))),
                )
                if available
            ],
            "remaining_monthly_asset_budget": build_plan.get("remaining_monthly_asset_budget"),
            "remaining_site_asset_budget": build_plan.get("remaining_site_asset_budget"),
        },
    )

    # Phase 1: Build variants in parallel (Claude agents + v0.dev)
    variants = await _build_variants(brief, asset_pack, build_plan)

    if not variants:
        # Fallback: single v0.dev build (original behavior)
        logger.warning("No variants produced, falling back to single v0.dev build")
        return await _v0_build_and_deploy(brief, business_name, site_type)

    # Phase 2: Opus reviews and cherry-picks
    synthesis = await _review_and_synthesize(variants, brief)

    # Phase 3: Build final page from synthesis
    final_html = await _build_final(synthesis, brief, asset_pack, build_plan)

    if not final_html:
        # Fallback: use the best variant directly
        best = synthesis.get("best_overall", 0)
        if best < len(variants) and variants[best].get("html"):
            final_html = variants[best]["html"]

    if not final_html:
        # Last resort: direct v0.dev build
        return await _v0_build_and_deploy(brief, business_name, site_type)

    # Anti-slop quality gate (behind ENABLE_ANTI_SLOP flag)
    final_html, slop_passed = await _anti_slop_site_gate(final_html, lead.get("id", "unknown"))
    if not slop_passed:
        logger.error("Site build blocked by anti-slop gate for %s (secrets detected)", business_name)

    # Neuro-scorer gate (behind ENABLE_NEURO_SCORER flag) — scores site copy on
    # 4 cognitive dimensions: self-relevance, trust, cognitive ease, emotional resonance
    try:
        if os.environ.get("ENABLE_NEURO_SCORER", "").lower() in ("true", "1", "yes"):
            from titan.neuro.neuro_scorer import NeuroScorer
            scorer = NeuroScorer()
            sections = _extract_text_sections(final_html)
            for section in sections[:10]:  # Cap at 10 sections to limit latency
                neuro = await scorer.score(section)
                if neuro.composite < 0.3:
                    logger.info(
                        "Neuro-scorer: low composite (%.2f) on site section for %s — "
                        "weak dims: %s",
                        neuro.composite,
                        business_name,
                        [d for d in ("self_relevance", "trust", "cognitive_ease", "emotional_resonance")
                         if getattr(neuro, d, 0.5) < 0.4],
                    )
    except ImportError:
        pass  # Neuro-scorer not available — skip silently
    except Exception as e:
        logger.debug("Neuro-scorer gate skipped for %s: %s", business_name, e)
        return ""

    # Phase 4: Deploy
    # Track the actual deploy method — not just what we attempted.
    actual_method = "5_agent_process"
    deployed_pages = ["index.html"]
    if page_count > 1:
        # Multi-page full site → generate inner pages + deploy to Netlify
        try:
            pages = await _generate_additional_pages(final_html, lead, brief, build_plan)
            from clawdbot.netlify_deploy import deploy_static_site
            url = await deploy_static_site(
                pages,
                business_name,
                client_id=lead.get("id"),
            )
            actual_method = "multipage_netlify"
            deployed_pages = list(pages.keys())
        except Exception as e:
            logger.warning("Multi-page deploy failed (%s), falling back to v0.dev with index only", e)
            url = await _deploy_to_v0(final_html, business_name, site_type)
            actual_method = "v0_fallback_from_netlify_failure"
            deployed_pages = ["index.html"]
    else:
        # Single-page demo → deploy to v0.dev
        url = await _deploy_to_v0(final_html, business_name, site_type)

    if url:
        await emit_event("site_build_completed", {
            "client_id": lead.get("id"),
            "business_name": business_name,
            "url": url,
            "site_type": site_type,
            "variants_built": len(variants),
            "method": actual_method,
            "build_mode": build_plan.get("build_mode"),
            "runtime_profile": build_plan.get("runtime_profile"),
            "assets_generated": asset_pack.get("assets_generated", 0),
            "pages": deployed_pages,
        })
        return url

    # Last resort: direct v0.dev build
    return await _v0_build_and_deploy(brief, business_name, site_type)


async def _build_product_brief(
    lead: dict,
    site_type: str,
    page_count: int,
    *,
    strategy: dict[str, Any] | None = None,
    build_plan: dict[str, Any] | None = None,
) -> str:
    """Build the product brief from lead data plus an AI-generated strategy layer."""
    pages = "Home (landing page)" if page_count == 1 else "Home, About, Services, Gallery/Portfolio, Contact"
    strategy = strategy or await _generate_reference_strategy(lead, site_type, page_count)
    build_plan = build_plan or await _resolve_build_plan(lead, site_type=site_type, page_count=page_count, strategy=strategy)
    reference_patterns = ", ".join(strategy.get("reference_patterns", [])[:5]) or "No explicit references supplied"
    sections = ", ".join(strategy.get("sections", [])[:8]) or pages
    anti_patterns = ", ".join(strategy.get("anti_patterns", [])[:5]) or "Avoid generic template feel"
    design_sources = strategy.get("design_sources", []) if isinstance(strategy.get("design_sources"), list) else []
    design_source_titles = ", ".join(
        f"{item.get('source', 'source')}: {item.get('title', item.get('slug', 'untitled'))}"
        for item in design_sources[:4]
        if isinstance(item, dict)
    ) or "No curated design sources resolved"
    adaptation_rules = strategy.get("design_source_rules", []) if isinstance(strategy.get("design_source_rules"), list) else []
    skill_guidance = strategy.get("skill_guidance", {}) if isinstance(strategy.get("skill_guidance"), dict) else {}
    skill_lines = []
    if skill_guidance:
        skill_lines = [
            "",
            "DESIGN SKILL GUIDANCE:",
            f"- Source skill: {skill_guidance.get('source_skill', 'ui-ux-pro-max')}",
            f"- Style direction: {skill_guidance.get('style_direction', 'High-trust premium')} ",
            f"- Palette: {skill_guidance.get('palette', 'Muted neutrals with one strong CTA accent')}",
            f"- Typography: {skill_guidance.get('typography', 'Expressive display + clear sans body')}",
            f"- UX notes: {', '.join(skill_guidance.get('ux_notes', [])[:4]) or 'Prioritize proof near CTA and responsive clarity'}",
        ]
    authored_assets = _resolve_authored_assets(lead, strategy)
    authored_asset_labels = ", ".join(
        label
        for label, available in (
            ("Rive", bool(authored_assets.get("rive_assets"))),
            ("Spline", bool(authored_assets.get("spline_assets"))),
            ("Theatre.js notes", bool(authored_assets.get("theatre_sequences"))),
        )
        if available
    ) or "None"

    return f"""PRODUCT BRIEF:
Business: {lead.get('business_name', 'Business')}
Industry: {lead.get('industry', 'general services')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Contact: {lead.get('contact_name', '')}
Research: {lead.get('research_summary', '')[:500]}
Site type: {site_type} ({page_count} page{'s' if page_count > 1 else ''})
Pages: {pages}
Requirements: Mobile responsive, fast loading, professional, conversion-optimized
Include: Hero section, services, contact form, testimonials, footer with business info

REFERENCE STRATEGY:
- Market position: {strategy.get('market_position', 'credible local leader')}
- Audience: {strategy.get('audience', 'buyers ready to compare providers')}
- Visual direction: {strategy.get('visual_direction', 'high-trust premium clarity')}
- Copy angle: {strategy.get('copy_angle', 'benefit-led and specific')}
- Conversion strategy: {strategy.get('conversion_strategy', 'strong CTA with proof nearby')}
- Governing idea: {strategy.get('governing_idea', build_plan.get('governing_idea', 'A site with one memorable idea, not just nice sections'))}
- Emotional target: {strategy.get('emotional_target', build_plan.get('emotional_target', 'Instant trust with a little intrigue'))}
- Signature moments: {', '.join(strategy.get('signature_moments', build_plan.get('signature_moments', []))[:4]) or 'Use one memorable motion or composition moment'}
- Reference patterns to adapt: {reference_patterns}
- Recommended sections: {sections}
- Avoid: {anti_patterns}
- Curated design sources: {design_source_titles}
- Source adaptation rules: {', '.join(adaptation_rules[:4]) or 'Adapt inspiration into original code'}

BUILD PLAN:
- Build mode: {build_plan.get('build_mode', 'conversion')}
- Runtime profile: {build_plan.get('runtime_profile', 'dom-motion')}
- Variant budget: {build_plan.get('variant_limit', 3)} variants max
- Paid assets allowed: {build_plan.get('allow_paid_assets', False)}
- Remaining asset budget for this build: ${build_plan.get('remaining_site_asset_budget', 0.0):.2f}
- Authored assets available: {authored_asset_labels}

Guardrails:
- Use references for pattern mining, not cloning.
- Do not copy exact HTML, copy, branding, images, or layouts.
- Make the page feel custom to this business, not like an industry template.
- Use shadcn/ui as the visual/component spine, adapted into original code.
- Reuse 21st.dev-style section patterns selectively when they improve conversion.
- Treat Stitch as art direction/prototyping input, not production truth.
- Never use Unsplash, Picsum, dummyimage, lorem ipsum, or generic placeholder assets in a prospect-facing or client-facing build.
- If no image asset is provided, design around typography, composition, SVG, gradients, and layout instead of inserting stock placeholders.
{(chr(10).join(skill_lines) + chr(10)) if skill_lines else ""}"""


def _extract_reference_urls(lead: dict) -> list[str]:
    """Collect explicit or embedded inspiration URLs without duplicates."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        normalized = url.strip().rstrip(".,);]")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    for key in ("reference_urls", "inspiration_urls", "competitor_urls"):
        value = lead.get(key)
        if isinstance(value, str):
            add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)

    for key in ("reference_sites", "inspiration_sites", "competitor_sites"):
        value = lead.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict) and item.get("url"):
                    add(str(item["url"]))

    summary = lead.get("research_summary", "") or ""
    for match in re.findall(r"https?://[^\s]+", summary):
        add(match)

    research_facts = _extract_research_facts(lead)
    for item in research_facts.get("reference_sites", []):
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict) and item.get("url"):
            add(str(item["url"]))

    return ordered


async def _generate_reference_strategy(lead: dict, site_type: str, page_count: int) -> dict[str, Any]:
    """Ask the model to turn lead context into reusable design patterns, not copied markup."""
    pages = "Home (landing page)" if page_count == 1 else "Home, About, Services, Gallery/Portfolio, Contact"
    reference_urls = _extract_reference_urls(lead)
    reference_block = "\n".join(f"- {url}" for url in reference_urls) if reference_urls else "- None supplied"
    research_facts = _extract_research_facts(lead)
    design_source_payload = await _resolve_design_sources_async(lead)
    reference_patterns = research_facts.get("reference_patterns", [])
    anti_patterns = research_facts.get("anti_patterns", [])
    design_positioning = research_facts.get("design_positioning", "")
    skill_guidance = await _get_design_skill_guidance(lead, site_type, page_count, reference_urls)
    skill_guidance_text = json.dumps(skill_guidance, indent=2) if skill_guidance else "{}"

    prompt = f"""You are ClawdBot's site strategist.

Build a reference strategy for a {site_type} site. Use the business context and any inspiration URLs below.
Extract reusable patterns only. Do NOT copy exact HTML, copy, branding, assets, or page layouts.
Use shadcn/ui as the production component spine, adapt 21st.dev-style blocks when useful, and treat Stitch as direction-only exploration.

BUSINESS:
- Name: {lead.get('business_name', 'Business')}
- Industry: {lead.get('industry', 'general services')}
- Location: {lead.get('city', '')}, {lead.get('country', '')}
- Site type: {site_type}
- Pages: {pages}
- Research summary: {lead.get('research_summary', '')[:1200]}

REFERENCE URLS:
{reference_block}

RESEARCH-DERIVED DESIGN INPUTS:
- Existing reference patterns: {json.dumps(reference_patterns) if reference_patterns else '[]'}
- Existing anti-patterns: {json.dumps(anti_patterns) if anti_patterns else '[]'}
- Existing design positioning: {design_positioning or '""'}

DESIGN SKILL GUIDANCE FROM ui-ux-pro-max:
{skill_guidance_text}

CURATED DESIGN SOURCES:
{json.dumps(design_source_payload.get("sources", []), indent=2)}

DESIGN SOURCE ADAPTATION RULES:
{json.dumps(design_source_payload.get("adaptation_rules", []), indent=2)}

Return JSON:
{{
  "market_position": "...",
  "audience": "...",
  "visual_direction": "...",
  "governing_idea": "...",
  "emotional_target": "...",
  "copy_angle": "...",
  "conversion_strategy": "...",
  "reference_patterns": ["..."],
  "sections": ["..."],
  "anti_patterns": ["..."],
  "signature_moments": ["..."],
  "build_mode": "conversion|premium-conversion|editorial-motion|surreal-product|retro-interface|audio-reactive",
  "runtime_profile": "dom-motion|gsap-lenis|retro-dom-motion|tone-pixi-lite|tone-pixi"
}}
"""

    fallback = {
        "market_position": "credible local operator",
        "audience": "buyers comparing a few providers before reaching out",
        "visual_direction": "clean, high-trust, premium but accessible",
        "governing_idea": "Use one clear visual thesis that makes the business feel intentional and specific",
        "emotional_target": "Trust in the first 5 seconds, then curiosity",
        "copy_angle": "specific benefits with clear local credibility",
        "conversion_strategy": "single primary CTA supported by proof and trust signals",
        "reference_patterns": ["clear hero promise", "proof near CTA", "service cards", "simple contact capture"],
        "sections": ["hero", "services", "proof", "FAQ", "contact"],
        "anti_patterns": ["copied branding", "generic stock-template feel", "cluttered multi-CTA hero"],
        "signature_moments": ["hero composition with real presence", "one memorable motion or visual reveal"],
        "build_mode": "conversion",
        "runtime_profile": "dom-motion",
        "design_sources": design_source_payload.get("sources", []),
        "design_source_rules": design_source_payload.get("adaptation_rules", []),
        "skill_guidance": skill_guidance,
    }

    try:
        result = await llm.generate(
            prompt=prompt,
            model="smart",
            max_tokens=1200,
            temperature=0.3,
            pipeline_stage="site_strategy",
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
        strategy = {**fallback, **parsed}
    except Exception as e:
        logger.warning(f"Reference strategy generation failed, using fallback: {e}")
        strategy = fallback

    try:
        await record_decision(
            agent="clawdbot",
            decision_type="site_reference_strategy",
            context={
                "client_id": lead.get("id"),
                "site_type": site_type,
                "page_count": page_count,
                "reference_count": len(reference_urls),
            },
            decision=strategy,
            reasoning="Pattern-mined inspiration brief with explicit anti-copy guardrails.",
        )
    except Exception:
        pass

    return strategy


def _resolve_design_sources(lead: dict) -> dict[str, Any]:
    """Resolve structured design sources from the local adapter (sync, text-only)."""
    try:
        from clawdbot.design_sources import resolve_design_sources
    except Exception as e:
        logger.debug(f"Design source adapter unavailable: {e}")
        return {"sources": [], "adaptation_rules": []}

    try:
        return resolve_design_sources(lead)
    except Exception as e:
        logger.warning(f"Design source resolution failed: {e}")
        return {"sources": [], "adaptation_rules": []}


async def _resolve_design_sources_async(lead: dict) -> dict[str, Any]:
    """Resolve design sources with real component code from 21st.dev.

    Tries the enriched async version first; falls back to sync text-only
    if the import or call fails.
    """
    try:
        from clawdbot.design_sources import resolve_design_sources_with_components
        return await resolve_design_sources_with_components(lead)
    except Exception as e:
        logger.warning("Enriched design source resolution failed, falling back to sync: %s", e)
        return _resolve_design_sources(lead)


def _format_component_snippets_block(snippets: list[dict[str, Any]]) -> str:
    """Format component snippets into a prompt block for variant generation.

    Returns a COMPONENT PATTERNS block if snippets exist, or empty string.
    """
    if not snippets:
        return ""
    lines = [
        "COMPONENT PATTERNS (use as structural HTML+Tailwind inspiration, NOT React copy):"
    ]
    for snippet in snippets:
        section = snippet.get("section", "unknown")
        code = snippet.get("code", "")
        if code:
            # Truncate individual snippets to keep prompt lean
            lines.append(f"\n--- {section.upper()} PATTERN ---")
            lines.append(code[:1200])
    return "\n".join(lines)


async def _get_design_skill_guidance(
    lead: dict,
    site_type: str,
    page_count: int,
    reference_urls: list[str],
) -> dict[str, Any]:
    """Use installed design skills for direction when available."""
    try:
        from shared.skill_loader import execute_skill, find_skill
    except Exception as e:
        logger.debug(f"Skill loader unavailable for site strategy: {e}")
        return {}

    if not find_skill("ui-ux-pro-max"):
        return {}

    prompt = f"""Design a concise website design system for this {site_type} build.

Business: {lead.get('business_name', 'Business')}
Industry: {lead.get('industry', 'general services')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Pages: {page_count}
Research: {lead.get('research_summary', '')[:800]}
Reference URLs: {json.dumps(reference_urls)}

Use a shadcn/ui-first mindset, mention 21st.dev-style block opportunities when useful, and optimize for an original high-conversion service website.

Return JSON:
{{
  "style_direction": "...",
  "palette": "...",
  "typography": "...",
  "ux_notes": ["..."]
}}"""

    try:
        result = await execute_skill(
            "ui-ux-pro-max",
            prompt,
            context={
                "site_type": site_type,
                "page_count": page_count,
                "reference_count": len(reference_urls),
            },
            model="smart",
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
        if isinstance(parsed, dict):
            parsed["source_skill"] = "ui-ux-pro-max"
            return parsed
    except Exception as e:
        logger.warning(f"ui-ux-pro-max guidance failed: {e}")

    return {}


async def _generate_logo(business_name: str, industry: str) -> str:
    """Generate a logo via Recraft if available. Returns URL or empty string."""
    try:
        from tools.recraft_client import generate_logo, get_recraft_status
        if not get_recraft_status().get("available"):
            return ""
        result = await generate_logo(business_name, industry)
        return result.get("url", "")
    except Exception as e:
        logger.debug(f"Logo generation skipped: {e}")
        return ""


async def _build_variants(brief: str, asset_pack: dict[str, Any], build_plan: dict[str, Any]) -> list[dict]:
    """Build the planned set of design variants in parallel."""
    direction_pool = list(DESIGN_DIRECTIONS)
    build_mode = str(build_plan.get("build_mode", "conversion"))
    runtime_profile = str(build_plan.get("runtime_profile", "dom-motion"))

    def prioritize(name: str) -> None:
        nonlocal direction_pool
        for index, direction in enumerate(direction_pool):
            if direction["name"] == name:
                direction_pool.insert(0, direction_pool.pop(index))
                break

    if runtime_profile in {"tone-pixi", "tone-pixi-lite"}:
        prioritize("audio-reactive-canvas")
    elif build_mode in {"surreal-product", "premium-conversion"}:
        prioritize("immersive-3d")
        prioritize("dark-cinematic")
    elif build_mode == "editorial-motion":
        prioritize("bold-editorial")

    directions = direction_pool[: max(1, int(build_plan.get("variant_limit", 3)))]

    tasks = []
    for direction in directions:
        tasks.append(_build_one_variant(direction, brief, asset_pack, build_plan))

    # Also run v0.dev as a competing agent if API key is set
    if os.getenv("V0_API_KEY", ""):
        tasks.append(_v0_variant(brief))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    variants: list[dict] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"Variant {i} failed: {result}")
            continue
        if isinstance(result, dict) and result.get("html"):
            variants.append(result)

    logger.info(f"Built {len(variants)}/{len(tasks)} variants successfully")
    return variants


async def _build_one_variant(
    direction: dict,
    brief: str,
    asset_pack: dict[str, Any],
    build_plan: dict[str, Any],
) -> dict:
    """Build a single variant using Claude with the design direction."""
    logo_url = asset_pack.get("logo_url", "")
    hero_url = asset_pack.get("hero_url", "")
    logo_line = f"\nLogo URL (use in the header): {logo_url}" if logo_url else ""
    hero_line = f"\nHero asset URL (use if it strengthens the concept): {hero_url}" if hero_url else ""

    # Extract component snippets from design sources for richer variant prompts
    all_snippets: list[dict[str, Any]] = []
    for src in build_plan.get("design_sources", []):
        if isinstance(src, dict):
            for snippet in src.get("component_snippets", []):
                if isinstance(snippet, dict) and snippet.get("code"):
                    all_snippets.append(snippet)
    component_block = _format_component_snippets_block(all_snippets)

    runtime_profile = build_plan.get("runtime_profile", "dom-motion")
    cdn_block = "\n".join(f"- {url}" for url in direction.get("cdn_deps", [])) or "- None required beyond standard HTML/CSS/JS"
    hint_block = "\n".join(f"- {hint}" for hint in direction.get("runtime_hints", [])) or "- Use tasteful light motion only where it helps."
    fallback_block = "\n".join(f"- {rule}" for rule in direction.get("fallback_rules", [])) or "- The page must still work if JavaScript fails."
    authored_asset_block = _format_authored_asset_guidance(asset_pack)

    is_multipage = build_plan.get("page_count", 1) > 1
    if is_multipage:
        page_type_line = "Build the HOME PAGE (index.html) of a multi-page website."
        nav_requirement = "- Include navigation linking to: Home (index.html), About (about.html), Services (services.html), Gallery (gallery.html), Contact (contact.html)"
        file_requirement = "- Single index.html file (other pages will be generated separately to match this design)"
    else:
        page_type_line = "Build a complete, production-ready landing page as a single index.html file."
        nav_requirement = ""
        file_requirement = "- Single index.html file"

    prompt = f"""{page_type_line}

DESIGN DIRECTION: {direction['name']}
Style: {direction['style']}
Colors: {direction['colors']}
Fonts: {direction['fonts']}
Layout: {direction['layout']}
Animation: {direction['animation']}
Copy angle: {direction['copy_angle']}
{logo_line}
{hero_line}

{brief}

{component_block}

TECHNICAL REQUIREMENTS:
{file_requirement} with Tailwind CSS CDN + Google Fonts
{nav_requirement}
- Use a shadcn/ui-inspired design system expressed in semantic HTML + Tailwind utilities
- Adapt 21st.dev-style block patterns only when they fit the business and remain original
- Use Stitch-like art direction exploration for bold visual hierarchy, but ship original code
- Runtime profile: {runtime_profile}
- You may include these runtime libraries via CDN script tags if they genuinely improve the concept:
{cdn_block}

MOTION & INTERACTION GUIDANCE:
{hint_block}

{authored_asset_block}

FALLBACK RULES:
{fallback_block}
- Mobile responsive with working hamburger menu
- Accessible: WCAG 2.1 AA contrast, semantic HTML, alt text
- Reduced motion support via prefers-reduced-motion
- All animations must respect prefers-reduced-motion
- All interactive elements must have non-JS fallbacks
- The hero must still look intentional if JavaScript fails to load
- Fast enough to be credible on a normal laptop and phone
- Never use Unsplash, Picsum, placehold, dummyimage, lorem ipsum, or other placeholders
- If no real asset URL is provided, use gradients, SVG, layout, and typography instead of fake stock imagery
- Never copy third-party branding, layouts, or markup directly
- Do NOT assume npm, bundlers, or a build step inside this single-file output

SEO REQUIREMENTS (mandatory for every page):
- <title> tag: business name + primary keyword + location (under 60 chars)
- <meta name="description"> with specific value prop (under 155 chars)
- <meta name="robots" content="index, follow">
- <link rel="canonical"> with full URL
- Open Graph tags: og:type, og:title, og:description, og:url, og:site_name, og:locale
- Twitter Card tags: twitter:card, twitter:title, twitter:description
- Schema.org JSON-LD: LocalBusiness or ProfessionalService type with name, description, address, priceRange, serviceType, and Offer entries for each service with price
- Semantic HTML: one <h1> per page, logical heading hierarchy (h1 > h2 > h3)
- Alt text on all images and SVGs with meaningful descriptions

Write the COMPLETE HTML. Start with <!DOCTYPE html> and end with </html>.
Output ONLY the HTML code, no explanation."""

    result = await llm.generate(
        prompt,
        model="smart",
        max_tokens=8000,
        temperature=0.7,
        pipeline_stage="site_build",
    )

    # Extract HTML from response
    html = _extract_html(result)
    if not html:
        return {"name": direction["name"], "html": ""}

    return {
        "name": direction["name"],
        "html": html,
        "direction": direction,
    }


async def _v0_variant(brief: str) -> dict:
    """Run v0.dev as one of the competing agents."""
    import httpx

    api_key = os.getenv("V0_API_KEY", "")
    if not api_key:
        return {"name": "v0-generated", "html": ""}

    v0_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    v0_base = "https://api.v0.dev/v1"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            proj_resp = await client.post(
                f"{v0_base}/projects",
                json={"name": "Perseus-variant"},
                headers=v0_headers,
            )
            proj_resp.raise_for_status()
            project_id = proj_resp.json().get("id", "")
            if not project_id:
                return {"name": "v0-generated", "html": ""}

            chat_resp = await client.post(
                f"{v0_base}/chats",
                json={"initialMessage": brief, "projectId": project_id},
                headers=v0_headers,
            )
            chat_resp.raise_for_status()
            chat_data = chat_resp.json()

            return {
                "name": "v0-generated",
                "html": "",  # v0 doesn't return raw HTML — it deploys directly
                "v0_project_id": project_id,
                "v0_chat_id": chat_data.get("id", ""),
                "v0_version_id": chat_data.get("versionId", chat_data.get("version_id", "")),
                "direction": {"name": "v0-generated", "style": "v0.dev AI-generated"},
            }
    except Exception as e:
        logger.warning(f"v0.dev variant failed: {e}")
        return {"name": "v0-generated", "html": ""}


async def _review_and_synthesize(variants: list[dict], brief: str) -> dict:
    """Opus reviews all variants and produces a cherry-pick synthesis plan."""
    variant_summaries = []
    for i, v in enumerate(variants):
        html_preview = v.get("html", "")[:2000] if v.get("html") else "(v0.dev deployment — no raw HTML)"
        variant_summaries.append(
            f"VARIANT {i}: {v['name']}\n"
            f"Direction: {v.get('direction', {}).get('style', 'unknown')}\n"
            f"HTML preview: {html_preview}\n"
        )

    prompt = f"""You are reviewing {len(variants)} landing page variants for a client.

{brief}

VARIANTS:
{'---'.join(variant_summaries)}

Score each variant 1-10 on:
1. Visual impact (first 50ms impression)
2. Copy quality (benefit-focused, pain-first)
3. CTA strategy (placement, text, urgency)
4. Layout and flow
5. Brand specificity / anti-template feel
6. Motion/interaction quality
7. Overall conversion potential

Then create a cherry-pick plan: which specific elements to take from each variant
for the final synthesized page.

Return JSON:
{{
    "scores": [
        {{"variant": 0, "name": "...", "visual": 8, "copy": 7, "cta": 9, "layout": 8, "overall": 8}},
        ...
    ],
    "best_overall": 0,
    "cherry_pick": {{
        "hero_from": 0,
        "copy_structure_from": 1,
        "color_palette_from": 2,
        "cta_strategy_from": 0,
        "animations_from": 3,
        "layout_from": 1,
        "reasoning": "..."
    }}
}}"""

    result = await llm.generate(
        prompt,
        model="genius",
        max_tokens=1500,
        temperature=0.2,
        pipeline_stage="site_review",
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        synthesis = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        synthesis = {"best_overall": 0, "cherry_pick": {"reasoning": "Parse failed, using first variant"}}

    # Record the review decision
    try:
        await record_decision(
            agent="clawdbot",
            decision_type="site_review",
            context={"variant_count": len(variants), "brief": brief[:300]},
            decision=synthesis,
            reasoning=synthesis.get("cherry_pick", {}).get("reasoning", ""),
        )
    except Exception:
        pass

    return synthesis


async def _build_final(
    synthesis: dict,
    brief: str,
    asset_pack: dict[str, Any],
    build_plan: dict[str, Any],
) -> str:
    """Build the final page using Ralph Loop — retry until QA checks pass."""
    from shared.execution_loop import Step, TaskPlan, execute_plan

    cherry = synthesis.get("cherry_pick", {})
    logo_url = asset_pack.get("logo_url", "")
    hero_url = asset_pack.get("hero_url", "")
    logo_line = f"\nLogo URL: {logo_url}" if logo_url else ""
    hero_line = f"\nHero asset URL: {hero_url}" if hero_url else ""
    runtime_profile = build_plan.get("runtime_profile", "dom-motion")
    cdn_block = "\n".join(f"- {url}" for url in build_plan.get("cdn_deps", [])) or "- None required beyond standard HTML/CSS/JS"
    hint_block = "\n".join(f"- {hint}" for hint in build_plan.get("runtime_hints", [])) or "- Keep motion restrained and useful."
    fallback_block = "\n".join(f"- {rule}" for rule in build_plan.get("fallback_rules", [])) or "- The page must still work if JavaScript fails."
    authored_asset_block = _format_authored_asset_guidance(asset_pack)

    is_multipage = build_plan.get("page_count", 1) > 1
    if is_multipage:
        page_type_instruction = "Build the FINAL production HOME PAGE (index.html) of a multi-page website, combining the best elements."
        nav_requirement = "- Navigation must link to: Home (index.html), About (about.html), Services (services.html), Gallery (gallery.html), Contact (contact.html)"
        file_requirement = "- Single index.html (other pages will be generated separately to match this design system)"
    else:
        page_type_instruction = "Build the FINAL production landing page combining the best elements."
        nav_requirement = ""
        file_requirement = "- Single index.html"

    build_prompt = f"""{page_type_instruction}

{brief}
{logo_line}
{hero_line}

SYNTHESIS PLAN:
{json.dumps(cherry, indent=2)}

Apply these conversion psychology principles:
- F-pattern layout optimization
- 5-second hero test (visitor understands the business instantly)
- Cognitive load reduction (whitespace, single CTA focus per section)
- Color psychology (60-30-10 rule, high-contrast CTA)
- Social proof near CTAs
- Pain-first copywriting (PAS framework)
- Price anchoring (show value before price)
- Risk reversal (guarantee, no contracts)

TECHNICAL REQUIREMENTS:
{file_requirement} with Tailwind CSS CDN + Google Fonts
{nav_requirement}
- Use a shadcn/ui-inspired component system translated into original HTML/Tailwind
- Borrow 21st.dev-style section ideas only as adapted patterns, never copied code
- Preserve the strongest Stitch-like visual direction from the synthesis plan
- Runtime profile: {runtime_profile}
- You may include these runtime libraries via CDN script tags if they genuinely improve the concept:
{cdn_block}

MOTION & INTERACTION GUIDANCE:
{hint_block}

{authored_asset_block}

FALLBACK RULES:
{fallback_block}
- Mobile responsive with hamburger menu
- WCAG 2.1 AA accessible
- prefers-reduced-motion support
- All animations must respect prefers-reduced-motion
- Interactive or immersive elements must have non-JS fallbacks
- If WebGL or audio fails, the hero must still work as a strong static composition
- Lazy load images
- Never ship placeholder images or placeholder copy
- No direct copying of markup, branding, or images from references

Write the COMPLETE HTML. Start with <!DOCTYPE html> and end with </html>.
Output ONLY the HTML code."""

    async def check_html_quality(result: str) -> dict:
        html = _extract_html(result)
        if not html:
            return {"passed": False, "error": "No valid HTML found in output"}
        analysis = await evaluate_site_experience(
            html=html,
            business_name="",
            site_type=str(build_plan.get("site_type", "demo")),
            context={
                "runtime_profile": runtime_profile,
                "build_mode": build_plan.get("build_mode", "conversion"),
                "governing_idea": build_plan.get("governing_idea", ""),
            },
        )
        issues = list(analysis.get("issues", []))
        if "tailwindcss" not in html.lower() and "tailwind" not in html.lower():
            issues.append("missing_tailwind")
        if "prefers-reduced-motion" not in html:
            issues.append("missing_reduced_motion")
        if issues:
            return {"passed": False, "error": "; ".join(issues[:8])}
        return {"passed": True}

    plan = TaskPlan(
        name="build_final_site",
        description="Build and QA the final synthesized landing page",
        agent="clawdbot",
        max_retries=3,
        steps=[
            Step(
                name="build_html",
                prompt=build_prompt,
                model="smart",
                max_tokens=8000,
                temperature=0.5,
                check=check_html_quality,
            ),
        ],
    )

    result = await execute_plan(plan)

    if result["completed"] and result["results"]:
        # execute_plan truncates to 2000 chars — we need the full step result
        full_result = plan.steps[0].result
        return _extract_html(full_result)

    # Fallback: single call without loop (original behavior)
    fallback = await llm.generate(
        build_prompt,
        model="smart",
        max_tokens=8000,
        temperature=0.5,
        pipeline_stage="site_build_final",
    )

    return _extract_html(fallback)


async def _deploy_to_v0(html: str, business_name: str, site_type: str) -> str:
    """Deploy HTML to v0.dev and return the live URL."""
    import httpx

    api_key = os.getenv("V0_API_KEY", "")
    if not api_key:
        logger.warning("V0_API_KEY not set — cannot deploy")
        return ""

    v0_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    v0_base = "https://api.v0.dev/v1"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            proj_resp = await client.post(
                f"{v0_base}/projects",
                json={"name": f"{site_type.title()} - {business_name[:30]}"},
                headers=v0_headers,
            )
            proj_resp.raise_for_status()
            project_id = proj_resp.json().get("id", "")
            if not project_id:
                return ""

            # Send the final HTML as the initial message
            chat_resp = await client.post(
                f"{v0_base}/chats",
                json={
                    "initialMessage": f"Deploy this exact HTML as a website:\n\n```html\n{html[:15000]}\n```",
                    "projectId": project_id,
                },
                headers=v0_headers,
            )
            chat_resp.raise_for_status()
            chat_data = chat_resp.json()
            chat_id = chat_data.get("id", "")
            version_id = chat_data.get("versionId", chat_data.get("version_id", ""))

            if version_id:
                deploy_resp = await client.post(
                    f"{v0_base}/deployments",
                    json={"projectId": project_id, "chatId": chat_id, "versionId": version_id},
                    headers=v0_headers,
                )
                deploy_resp.raise_for_status()
                url = deploy_resp.json().get("webUrl", deploy_resp.json().get("url", ""))
                if url:
                    logger.info(f"Deployed {site_type} site to v0.dev: {url}")
                    return url

    except Exception as e:
        logger.error(f"v0.dev deploy failed: {e}")

    return ""


async def _v0_build_and_deploy(brief: str, business_name: str, site_type: str) -> str:
    """Fallback: single v0.dev build + deploy (original behavior)."""
    import httpx

    api_key = os.getenv("V0_API_KEY", "")
    if not api_key:
        return ""

    v0_headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    v0_base = "https://api.v0.dev/v1"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            proj_resp = await client.post(
                f"{v0_base}/projects",
                json={"name": f"{site_type.title()} - {business_name[:30]}"},
                headers=v0_headers,
            )
            proj_resp.raise_for_status()
            project_id = proj_resp.json().get("id", "")
            if not project_id:
                return ""

            chat_resp = await client.post(
                f"{v0_base}/chats",
                json={"initialMessage": brief, "projectId": project_id},
                headers=v0_headers,
            )
            chat_resp.raise_for_status()
            chat_data = chat_resp.json()
            version_id = chat_data.get("versionId", chat_data.get("version_id", ""))

            if version_id:
                deploy_resp = await client.post(
                    f"{v0_base}/deployments",
                    json={
                        "projectId": project_id,
                        "chatId": chat_data.get("id", ""),
                        "versionId": version_id,
                    },
                    headers=v0_headers,
                )
                deploy_resp.raise_for_status()
                url = deploy_resp.json().get("webUrl", deploy_resp.json().get("url", ""))
                if url:
                    logger.info(f"v0.dev fallback deployed: {url}")
                    return url

    except Exception as e:
        logger.error(f"v0.dev fallback build failed: {e}")

    return ""


def _extract_html(text: str) -> str:
    """Extract HTML from LLM response, handling code fences."""
    # Try to extract from code block
    if "```html" in text:
        start = text.find("```html") + 7
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()

    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            candidate = text[start:end].strip()
            if candidate.startswith("<!DOCTYPE") or candidate.startswith("<html"):
                return candidate

    # Try raw HTML
    if "<!DOCTYPE" in text:
        start = text.find("<!DOCTYPE")
        end = text.rfind("</html>")
        if end > start:
            return text[start:end + 7].strip()

    if "<html" in text:
        start = text.find("<html")
        end = text.rfind("</html>")
        if end > start:
            return text[start:end + 7].strip()

    return ""
