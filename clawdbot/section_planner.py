"""Section decomposition and build planning for ClawdBot v2.

Turns a lead brief into a structured BuildPlan: which sections to generate,
in what order, with what content, using which design direction, and
constrained by which design tokens. Replaces the monolithic
``_build_product_brief()`` with a section-aware planner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from clawdbot.design_tokens import (
    CDN_STACK,
    DIRECTION_CDN_MAP,
    DesignTokens,
    build_head_block,
    extract_tokens_from_taste,
    tokens_to_css_vars,
    tokens_to_tailwind_config,
)
from clawdbot.snippet_registry import select_snippets_for_build
from clawdbot.taste_profile import (
    get_direction_weights,
    load_taste_profile,
)

logger = logging.getLogger("perseus.clawdbot.section_planner")

# ---------------------------------------------------------------------------
# Priority mapping: section_type -> generation priority
#   1 = critical (hero, CTA), 2 = important (services, features), 3 = standard
# ---------------------------------------------------------------------------

_SECTION_PRIORITY: dict[str, int] = {
    "hero": 1,
    "cta": 1,
    "services": 2,
    "features": 2,
    "pricing": 2,
    "testimonials": 2,
    "badges": 3,
    "faq": 3,
    "contact": 3,
    "navbar": 3,
    "footer": 3,
}

# ---------------------------------------------------------------------------
# Build tier system (Task 5)
# ---------------------------------------------------------------------------


@dataclass
class BuildTier:
    """Cost/quality tier for site generation."""

    name: str
    model: str
    max_sections: int
    max_iterations: int
    vlm_provider: str
    use_claude_final_qa: bool
    effects_enabled: bool
    estimated_cost_usd: float


DEMO_TIER = BuildTier(
    name="demo",
    model="ollama:qwen2.5-coder:14b",
    max_sections=4,
    max_iterations=1,
    vlm_provider="ollama",
    use_claude_final_qa=False,
    effects_enabled=False,
    estimated_cost_usd=0.00,
)

PREMIUM_TIER = BuildTier(
    name="premium",
    model="smart",
    max_sections=8,
    max_iterations=3,
    vlm_provider="ollama+claude",
    use_claude_final_qa=True,
    effects_enabled=True,
    estimated_cost_usd=0.19,
)


def select_build_tier(site_type: str) -> BuildTier:
    """Select build tier based on site type.

    demo -> DEMO_TIER ($0/site, fully local on Mac M4 Studio)
    full -> PREMIUM_TIER ($0.19/site via Claude API)
    """
    if site_type == "full":
        return PREMIUM_TIER
    return DEMO_TIER


# ---------------------------------------------------------------------------
# Industry section templates (Task 6)
# ---------------------------------------------------------------------------

INDUSTRY_SECTION_TEMPLATES: dict[str, list[str]] = {
    "dentist": ["navbar", "hero", "services", "badges", "testimonials", "faq", "contact", "footer"],
    "plumber": ["navbar", "hero", "badges", "services", "testimonials", "contact", "footer"],
    "restaurant": ["navbar", "hero", "features", "testimonials", "contact", "footer"],
    "saas": ["navbar", "hero", "features", "pricing", "testimonials", "cta", "faq", "footer"],
    "legal": ["navbar", "hero", "services", "testimonials", "faq", "contact", "footer"],
    "real_estate": ["navbar", "hero", "features", "testimonials", "contact", "footer"],
    "default": ["navbar", "hero", "services", "testimonials", "contact", "footer"],
}

# Sections that must survive truncation for demo tier (in priority order)
_DEMO_ESSENTIAL = ["hero", "services", "features", "contact", "footer"]


def get_section_template(industry: str, tier: BuildTier) -> list[str]:
    """Return ordered section list for an industry, respecting tier limits.

    For demo tier: truncates to ``tier.max_sections`` while preserving
    the most important sections (hero, services/features, contact, footer).
    """
    template = INDUSTRY_SECTION_TEMPLATES.get(
        industry.lower(),
        INDUSTRY_SECTION_TEMPLATES["default"],
    )

    if len(template) <= tier.max_sections:
        return list(template)

    # Keep essential sections in their original order, fill remaining budget
    essential_in_order = [s for s in template if s in _DEMO_ESSENTIAL]
    remaining = [s for s in template if s not in _DEMO_ESSENTIAL]

    budget = tier.max_sections
    result = essential_in_order[:budget]
    budget -= len(result)

    if budget > 0:
        result.extend(remaining[:budget])

    # Restore original ordering
    original_order = {s: i for i, s in enumerate(template)}
    result.sort(key=lambda s: original_order.get(s, 999))
    return result


# ---------------------------------------------------------------------------
# SectionPlan + BuildPlan dataclasses (Task 1)
# ---------------------------------------------------------------------------


@dataclass
class SectionPlan:
    """Plan for a single website section."""

    section_type: str
    order: int
    content: dict[str, Any]
    snippet_ref: str | None = None
    reference_image: str | None = None
    generation_priority: int = 3
    max_iterations: int = 1


@dataclass
class BuildPlan:
    """Complete structured plan for building a website."""

    sections: list[SectionPlan]
    design_tokens: DesignTokens
    design_contract: dict[str, Any]
    direction_name: str
    site_type: str
    page_count: int
    business_context: dict[str, Any]
    cdn_deps: list[str]
    total_sections: int = 0
    estimated_build_time_s: int = 0
    estimated_cost_usd: float = 0.0
    tier: BuildTier = field(default_factory=lambda: DEMO_TIER)


# ---------------------------------------------------------------------------
# Design contract generation (Task 2)
# ---------------------------------------------------------------------------


def generate_design_contract(
    tokens: DesignTokens,
    business_context: dict[str, Any],
    section_list: list[str],
) -> dict[str, Any]:
    """Generate the shared design contract for section agents.

    The contract is the single source of truth that every section agent
    receives, ensuring visual consistency across independently generated
    sections.
    """
    nav_links = _infer_nav_links(section_list, business_context)

    return {
        "palette": {
            "primary": tokens.primary,
            "secondary": tokens.secondary,
            "accent": tokens.accent,
            "bg": tokens.background,
            "surface": tokens.surface,
            "text": tokens.text_primary,
            "text_secondary": tokens.text_secondary,
            "text_muted": tokens.text_muted,
        },
        "typography": {
            "heading_font": tokens.font_display,
            "body_font": tokens.font_body,
            "scale": tokens.scale_ratio,
            "heading_weight": tokens.heading_weight,
            "body_line_height": tokens.body_line_height,
        },
        "spacing": {
            "section_padding": tokens.section_padding_y,
            "container": f"max-w-[{tokens.container_max_width}] mx-auto px-[{tokens.container_padding_x}]",
            "card_gap": tokens.card_gap,
        },
        "nav": {
            "style": "fixed-top-blur",
            "links": nav_links,
        },
        "footer": {
            "style": "minimal-centered",
            "links": ["Privacy", "Terms"],
        },
        "motion": {
            "scroll_trigger": tokens.animation_enabled,
            "entrance": tokens.entrance_style,
            "duration": tokens.transition_duration,
        },
        "brand": {
            "name": business_context.get("name", ""),
            "tagline": business_context.get("tagline", ""),
            "industry": business_context.get("industry", ""),
        },
        "css_vars_block": tokens_to_css_vars(tokens),
        "head_block": build_head_block(tokens),
        "tailwind_config": tokens_to_tailwind_config(tokens),
    }


def _infer_nav_links(section_list: list[str], ctx: dict[str, Any]) -> list[str]:
    """Build nav link labels from the section list."""
    label_map = {
        "hero": None,  # hero is the landing, not a nav link
        "navbar": None,
        "footer": None,
        "services": "Services",
        "features": "Features",
        "pricing": "Pricing",
        "testimonials": "Testimonials",
        "contact": "Contact",
        "faq": "FAQ",
        "badges": "Trust",
        "cta": "Get Started",
    }
    links: list[str] = []
    for section in section_list:
        label = label_map.get(section, section.replace("_", " ").title())
        if label is not None:
            links.append(label)
    return links


# ---------------------------------------------------------------------------
# Content extraction from lead data (Task 3)
# ---------------------------------------------------------------------------


def extract_section_content(
    lead: dict[str, Any],
    section_type: str,
    research_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract content for a specific section type from lead data.

    For missing data: returns what we have with ``_missing`` keys listing
    fields that need LLM generation.
    """
    research = research_facts or {}
    extractor = _CONTENT_EXTRACTORS.get(section_type, _extract_generic)
    return extractor(lead, research)


def _extract_hero(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    name = lead.get("business_name") or lead.get("name") or ""
    tagline = lead.get("tagline") or lead.get("headline") or ""
    industry = lead.get("industry", "")

    content: dict[str, Any] = {
        "business_name": name,
        "headline": tagline or f"Welcome to {name}" if name else "",
        "subheadline": lead.get("description", ""),
        "cta_text": lead.get("cta_text", "Get Started"),
        "hero_image_url": lead.get("hero_image_url", ""),
    }

    missing = []
    if not content["headline"]:
        missing.append("headline")
    if not content["subheadline"]:
        missing.append("subheadline")
    if missing:
        content["_missing"] = missing
        content["_generation_context"] = {
            "industry": industry,
            "business_name": name,
        }
    return content


def _extract_services(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    services = lead.get("services") or research.get("services") or []
    if isinstance(services, str):
        services = [{"name": s.strip(), "description": ""} for s in services.split(",") if s.strip()]
    elif isinstance(services, list):
        normalized: list[dict[str, str]] = []
        for s in services:
            if isinstance(s, str):
                normalized.append({"name": s, "description": ""})
            elif isinstance(s, dict):
                normalized.append({"name": s.get("name", ""), "description": s.get("description", "")})
        services = normalized

    content: dict[str, Any] = {"services": services}
    if not services:
        content["_missing"] = ["services"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_features(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    # Features and services share similar structure
    features = lead.get("features") or lead.get("services") or research.get("features") or []
    if isinstance(features, str):
        features = [{"name": f.strip(), "description": ""} for f in features.split(",") if f.strip()]
    elif isinstance(features, list):
        normalized: list[dict[str, str]] = []
        for f in features:
            if isinstance(f, str):
                normalized.append({"name": f, "description": ""})
            elif isinstance(f, dict):
                normalized.append({"name": f.get("name", ""), "description": f.get("description", "")})
        features = normalized

    content: dict[str, Any] = {"features": features}
    if not features:
        content["_missing"] = ["features"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_testimonials(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    testimonials = lead.get("testimonials") or research.get("testimonials") or []
    if isinstance(testimonials, list):
        normalized: list[dict[str, Any]] = []
        for t in testimonials:
            if isinstance(t, str):
                normalized.append({"text": t, "client_name": "", "rating": 5})
            elif isinstance(t, dict):
                normalized.append({
                    "text": t.get("text", ""),
                    "client_name": t.get("client_name", t.get("name", "")),
                    "rating": t.get("rating", 5),
                })
        testimonials = normalized

    content: dict[str, Any] = {"testimonials": testimonials}
    if not testimonials:
        content["_missing"] = ["testimonials"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_contact(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    return {
        "phone": lead.get("phone", ""),
        "email": lead.get("email", ""),
        "address": lead.get("address", ""),
        "hours": lead.get("hours") or research.get("hours", ""),
    }


def _extract_pricing(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    pricing = lead.get("pricing") or research.get("pricing") or []
    content: dict[str, Any] = {"pricing": pricing}
    if not pricing:
        content["_missing"] = ["pricing"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_faq(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    faq = lead.get("faq") or research.get("faq") or []
    content: dict[str, Any] = {"faq": faq}
    if not faq:
        content["_missing"] = ["faq"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_navbar(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_name": lead.get("business_name") or lead.get("name", ""),
        "nav_links": lead.get("nav_links", []),
    }


def _extract_footer(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_name": lead.get("business_name") or lead.get("name", ""),
        "phone": lead.get("phone", ""),
        "email": lead.get("email", ""),
        "address": lead.get("address", ""),
        "social_links": lead.get("social_links") or research.get("social_links") or [],
    }


def _extract_badges(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    badges = lead.get("badges") or research.get("badges") or []
    content: dict[str, Any] = {"badges": badges}
    if not badges:
        content["_missing"] = ["badges"]
        content["_generation_context"] = {"industry": lead.get("industry", "")}
    return content


def _extract_cta(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": lead.get("cta_headline", ""),
        "subheadline": lead.get("cta_subheadline", ""),
        "cta_text": lead.get("cta_text", "Get Started"),
        "cta_url": lead.get("cta_url", "#contact"),
    }


def _extract_generic(lead: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    return {"_missing": ["content"], "_generation_context": {"industry": lead.get("industry", "")}}


_CONTENT_EXTRACTORS: dict[str, Any] = {
    "hero": _extract_hero,
    "services": _extract_services,
    "features": _extract_features,
    "testimonials": _extract_testimonials,
    "contact": _extract_contact,
    "pricing": _extract_pricing,
    "faq": _extract_faq,
    "navbar": _extract_navbar,
    "footer": _extract_footer,
    "badges": _extract_badges,
    "cta": _extract_cta,
}


# ---------------------------------------------------------------------------
# LLM content generation for missing fields (Task 3 cont.)
# ---------------------------------------------------------------------------


async def _fill_missing_content(content: dict[str, Any], section_type: str) -> dict[str, Any]:
    """Use LLM (fast tier) to generate missing content fields.

    Only called when content has ``_missing`` key indicating fields that
    need generation. Uses Haiku/fast model for lowest cost.
    """
    missing = content.get("_missing")
    if not missing:
        return content

    gen_ctx = content.get("_generation_context", {})
    industry = gen_ctx.get("industry", "business")
    business_name = gen_ctx.get("business_name", "")

    try:
        from shared.llm_client import llm

        prompt = _build_generation_prompt(section_type, missing, industry, business_name)
        raw = await llm.generate(
            prompt,
            model="fast",
            max_tokens=800,
            temperature=0.7,
            pipeline_stage="section_planner_content_fill",
        )

        import json
        try:
            generated = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Try to extract JSON from response
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                generated = json.loads(match.group())
            else:
                logger.warning("Failed to parse LLM content generation response for %s", section_type)
                generated = {}

        for field_name in missing:
            if field_name in generated:
                content[field_name] = generated[field_name]

    except ImportError:
        logger.warning("shared.llm_client not available; leaving missing content for %s", section_type)
    except Exception:
        logger.warning("Content generation failed for %s", section_type, exc_info=True)

    # Clean up internal keys
    content.pop("_missing", None)
    content.pop("_generation_context", None)
    return content


def _build_generation_prompt(
    section_type: str,
    missing_fields: list[str],
    industry: str,
    business_name: str,
) -> str:
    """Build an LLM prompt to generate missing section content."""
    name_ctx = f" called '{business_name}'" if business_name else ""
    fields_str = ", ".join(missing_fields)

    prompts: dict[str, str] = {
        "hero": (
            f"Generate website hero section content for a {industry} business{name_ctx}. "
            f"Missing fields: {fields_str}. "
            "Return JSON with: headline (compelling, 6-10 words), "
            "subheadline (one sentence value prop)."
        ),
        "services": (
            f"Generate 4 realistic services for a {industry} business{name_ctx}. "
            'Return JSON: {{"services": [{{"name": "...", "description": "one sentence"}}]}}'
        ),
        "features": (
            f"Generate 4 key features/benefits for a {industry} business{name_ctx}. "
            'Return JSON: {{"features": [{{"name": "...", "description": "one sentence"}}]}}'
        ),
        "testimonials": (
            f"Generate 3 realistic testimonials for a {industry} business{name_ctx}. "
            'Return JSON: {{"testimonials": [{{"text": "...", "client_name": "First L.", "rating": 5}}]}}'
        ),
        "pricing": (
            f"Generate 3 realistic pricing tiers for a {industry} business{name_ctx}. "
            'Return JSON: {{"pricing": [{{"name": "...", "price": "$X", "features": ["..."]}}]}}'
        ),
        "faq": (
            f"Generate 4 common FAQs for a {industry} business{name_ctx}. "
            'Return JSON: {{"faq": [{{"question": "...", "answer": "..."}}]}}'
        ),
        "badges": (
            f"Generate 4 trust badges/credentials for a {industry} business{name_ctx}. "
            'Return JSON: {{"badges": [{{"text": "Licensed & Insured", "icon": "shield"}}]}}'
        ),
    }

    return prompts.get(section_type, (
        f"Generate content for a '{section_type}' website section for a {industry} "
        f"business{name_ctx}. Missing: {fields_str}. Return JSON with the missing fields."
    ))


# ---------------------------------------------------------------------------
# VLM reference decomposition (Task 4)
# ---------------------------------------------------------------------------


async def decompose_reference_site(
    url: str,
    pool: Any = None,
) -> list[dict[str, Any]]:
    """Screenshot a reference URL, use VLM to identify section boundaries.

    Args:
        url: Reference site URL to decompose.
        pool: Optional PlaywrightPool (uses taste_analyzer screenshot if None).

    Returns:
        List of section dicts with type, y_start_pct, y_end_pct,
        description, dominant_colors.
    """
    # Capture screenshot
    png_bytes = await _capture_reference_screenshot(url, pool)
    if not png_bytes:
        logger.warning("Failed to capture screenshot of %s", url)
        return []

    # Ask VLM to decompose
    try:
        from clawdbot.visual_scorer import _vlm_score

        prompt = (
            "Analyze this full-page website screenshot. Identify each distinct visual section "
            "(navbar, hero, features, services, testimonials, pricing, CTA, contact, footer, etc.).\n\n"
            "For each section, estimate its vertical position as a percentage of the total page height.\n\n"
            "Return ONLY valid JSON: "
            '[{"type": "hero", "y_start_pct": 0, "y_end_pct": 25, '
            '"description": "Split hero with headline left, image right", '
            '"dominant_colors": ["#1a1a2e", "#e94560"]}, ...]'
        )

        data = await _vlm_score([png_bytes], prompt)

        # Handle various response shapes
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            sections = data.get("sections", [])
            if isinstance(sections, list):
                return sections
            # Try to find any list value in the dict
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        return []

    except ImportError:
        logger.warning("visual_scorer not available for VLM decomposition")
        return []
    except Exception:
        logger.warning("VLM decomposition failed for %s", url, exc_info=True)
        return []


async def _capture_reference_screenshot(url: str, pool: Any = None) -> bytes | None:
    """Capture a full-page screenshot of a URL."""
    # Try PlaywrightPool first
    if pool is not None:
        try:
            return await pool.render(
                f'<script>window.location.href="{url}"</script>',
                wait_ms=5000,
            )
        except Exception:
            logger.debug("Pool capture failed; falling back", exc_info=True)

    # Fall back to taste_analyzer._screenshot_url
    try:
        from clawdbot.taste_analyzer import _screenshot_url
        return await _screenshot_url(url)
    except ImportError:
        logger.warning("taste_analyzer not available for screenshot capture")
    except Exception:
        logger.warning("Screenshot capture failed for %s", url, exc_info=True)

    return None


# ---------------------------------------------------------------------------
# Main planner (Task 1 core)
# ---------------------------------------------------------------------------


def _select_best_direction(weights: dict[str, float]) -> str:
    """Pick the direction with the highest taste weight."""
    if not weights:
        return "cosmos-dark-curation"
    return max(weights, key=lambda k: weights[k])


def _build_business_context(lead: dict[str, Any]) -> dict[str, Any]:
    """Extract business context from lead data."""
    return {
        "name": lead.get("business_name") or lead.get("name", ""),
        "industry": lead.get("industry", ""),
        "tagline": lead.get("tagline", ""),
        "description": lead.get("description", ""),
        "services": lead.get("services", []),
        "phone": lead.get("phone", ""),
        "email": lead.get("email", ""),
        "address": lead.get("address", ""),
    }


def _estimate_build_time(tier: BuildTier, section_count: int) -> int:
    """Estimate build time in seconds."""
    # Demo: ~30s per section (local inference)
    # Premium: ~60s per section (API + iterations)
    per_section = 30 if tier.name == "demo" else 60
    return per_section * section_count


async def generate_build_plan(
    lead: dict[str, Any],
    site_type: str = "demo",
    page_count: int = 1,
) -> BuildPlan:
    """Generate a structured build plan from lead data.

    1. Load taste profile, select best direction via get_direction_weights
    2. Extract DesignTokens from direction + taste overlay
    3. Select build tier (demo vs premium) based on site_type
    4. Determine section list from industry template
    5. For each section: populate content from lead data, select snippet, set iteration budget
    6. Generate design contract
    7. Return complete BuildPlan
    """
    # 1. Load taste and pick direction
    taste_profile = load_taste_profile()
    weights = get_direction_weights(taste_profile)
    direction_name = _select_best_direction(weights)

    # Build a minimal direction dict for token extraction
    direction_dict: dict[str, Any] = {"name": direction_name}
    try:
        from clawdbot.site_builder import DESIGN_DIRECTIONS
        for d in DESIGN_DIRECTIONS:
            if d.get("name") == direction_name:
                direction_dict = d
                break
    except ImportError:
        logger.debug("site_builder not available; using direction name only")

    # 2. Extract tokens with taste overlay
    tokens = extract_tokens_from_taste(taste_profile, direction_dict)

    # 3. Select tier
    tier = select_build_tier(site_type)

    # 4. Determine section list
    industry = lead.get("industry", "default")
    section_list = get_section_template(industry, tier)

    # 5. Build section plans
    snippet_refs = select_snippets_for_build(
        section_list,
        direction_name,
        taste_weights=weights,
    )

    research_facts = _extract_research_facts_from_lead(lead)
    business_context = _build_business_context(lead)

    section_plans: list[SectionPlan] = []
    for order, section_type in enumerate(section_list):
        priority = _SECTION_PRIORITY.get(section_type, 3)
        content = extract_section_content(lead, section_type, research_facts)

        # Fill missing content via LLM
        if "_missing" in content:
            content = await _fill_missing_content(content, section_type)

        # Iteration budget based on tier + priority
        if tier.name == "demo":
            max_iter = 1
        else:
            max_iter = {1: 3, 2: 2, 3: 1}.get(priority, 1)

        section_plans.append(SectionPlan(
            section_type=section_type,
            order=order,
            content=content,
            snippet_ref=snippet_refs.get(section_type),
            reference_image=None,
            generation_priority=priority,
            max_iterations=max_iter,
        ))

    # 6. Generate design contract
    contract = generate_design_contract(tokens, business_context, section_list)

    # 7. Build CDN deps list
    cdn_keys = DIRECTION_CDN_MAP.get(direction_name, ["gsap", "scrolltrigger"])
    cdn_deps = [CDN_STACK[k] for k in cdn_keys if k in CDN_STACK]

    total_sections = len(section_plans)
    est_time = _estimate_build_time(tier, total_sections)
    est_cost = tier.estimated_cost_usd

    return BuildPlan(
        sections=section_plans,
        design_tokens=tokens,
        design_contract=contract,
        direction_name=direction_name,
        site_type=site_type,
        page_count=page_count,
        business_context=business_context,
        cdn_deps=cdn_deps,
        total_sections=total_sections,
        estimated_build_time_s=est_time,
        estimated_cost_usd=est_cost,
        tier=tier,
    )


def _extract_research_facts_from_lead(lead: dict[str, Any]) -> dict[str, Any]:
    """Extract research_facts dict from lead, handling string/dict/None."""
    import json as _json

    raw = lead.get("research_facts")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, _json.JSONDecodeError):
            pass
    return {}
