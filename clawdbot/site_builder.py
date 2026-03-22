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
from typing import Any

from shared.comms import record_decision
from shared.db import emit_event
from shared.llm_client import llm

logger = logging.getLogger("perseus.clawdbot.site_builder")

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
    },
    {
        "name": "bold-editorial",
        "style": "Magazine editorial, oversized serif type, asymmetric columns",
        "colors": "Rich navy + warm cream + gold accent",
        "fonts": "Instrument Serif (display) + Source Serif Pro (body)",
        "layout": "Multi-column editorial grid, split hero, pull-quotes",
        "animation": "Text reveals on scroll, parallax on images",
        "copy_angle": "Storytelling, PAS framework",
    },
    {
        "name": "dark-cinematic",
        "style": "OLED luxury, cinematic lighting, premium noir",
        "colors": "True black + silver/chrome + electric blue glow",
        "fonts": "Clash Display (display) + Outfit (body)",
        "layout": "Full-bleed dark sections, floating glow cards",
        "animation": "Glow pulses, particles, cursor effects",
        "copy_angle": "Bold, aspirational, power words",
    },
    {
        "name": "organic-illustrated",
        "style": "Biomorphic, hand-crafted, natural textures, warm",
        "colors": "Warm sand + forest green + terracotta accent",
        "fonts": "Fraunces (display) + Work Sans (body)",
        "layout": "Flowing sections with organic wave dividers, rounded cards",
        "animation": "Gentle fades, SVG path drawing",
        "copy_angle": "Warm, conversational, rhetorical questions",
    },
    {
        "name": "playful-animated",
        "style": "Motion-forward, vibrant energy, bold color blocks",
        "colors": "Indigo + hot pink + lime accent on white",
        "fonts": "Cabinet Grotesk (display) + Plus Jakarta Sans (body)",
        "layout": "Bento grid layout, diagonal section breaks",
        "animation": "Bouncy springs, hover scale, count-ups, gradient mesh",
        "copy_angle": "Energetic, fun, analogies",
    },
]


async def build_demo_site(lead: dict) -> str:
    """Build a demo landing page for a prospect. Returns deployed URL or empty string."""
    return await _build_site(lead, site_type="demo", page_count=1)


async def build_full_site(lead: dict) -> str:
    """Build a full 5-page website for a closed deal. Returns deployed URL or empty string."""
    return await _build_site(lead, site_type="full", page_count=5)


async def _build_site(lead: dict, *, site_type: str, page_count: int) -> str:
    """Core build process: generate variants → review → synthesize → deploy."""
    business_name = lead.get("business_name", "Business")
    industry = lead.get("industry", "general services")

    brief = await _build_product_brief(lead, site_type, page_count)

    # Generate a logo via Recraft if available
    logo_url = await _generate_logo(business_name, industry)

    # Phase 1: Build variants in parallel (Claude agents + v0.dev)
    variants = await _build_variants(brief, logo_url, site_type)

    if not variants:
        # Fallback: single v0.dev build (original behavior)
        logger.warning("No variants produced, falling back to single v0.dev build")
        return await _v0_build_and_deploy(brief, business_name, site_type)

    # Phase 2: Opus reviews and cherry-picks
    synthesis = await _review_and_synthesize(variants, brief)

    # Phase 3: Build final page from synthesis
    final_html = await _build_final(synthesis, brief, logo_url)

    if not final_html:
        # Fallback: use the best variant directly
        best = synthesis.get("best_overall", 0)
        if best < len(variants) and variants[best].get("html"):
            final_html = variants[best]["html"]

    # Phase 4: Deploy via v0.dev
    if final_html:
        url = await _deploy_to_v0(final_html, business_name, site_type)
        if url:
            await emit_event("site_build_completed", {
                "client_id": lead.get("id"),
                "business_name": business_name,
                "url": url,
                "site_type": site_type,
                "variants_built": len(variants),
                "method": "5_agent_process",
            })
            return url

    # Last resort: direct v0.dev build
    return await _v0_build_and_deploy(brief, business_name, site_type)


async def _build_product_brief(lead: dict, site_type: str, page_count: int) -> str:
    """Build the product brief from lead data plus an AI-generated strategy layer."""
    pages = "Home (landing page)" if page_count == 1 else "Home, About, Services, Gallery/Portfolio, Contact"
    strategy = await _generate_reference_strategy(lead, site_type, page_count)
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
- Reference patterns to adapt: {reference_patterns}
- Recommended sections: {sections}
- Avoid: {anti_patterns}
- Curated design sources: {design_source_titles}
- Source adaptation rules: {', '.join(adaptation_rules[:4]) or 'Adapt inspiration into original code'}

Guardrails:
- Use references for pattern mining, not cloning.
- Do not copy exact HTML, copy, branding, images, or layouts.
- Make the page feel custom to this business, not like an industry template.
- Use shadcn/ui as the visual/component spine, adapted into original code.
- Reuse 21st.dev-style section patterns selectively when they improve conversion.
- Treat Stitch as art direction/prototyping input, not production truth.
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
    design_source_payload = _resolve_design_sources(lead)
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
  "copy_angle": "...",
  "conversion_strategy": "...",
  "reference_patterns": ["..."],
  "sections": ["..."],
  "anti_patterns": ["..."]
}}
"""

    fallback = {
        "market_position": "credible local operator",
        "audience": "buyers comparing a few providers before reaching out",
        "visual_direction": "clean, high-trust, premium but accessible",
        "copy_angle": "specific benefits with clear local credibility",
        "conversion_strategy": "single primary CTA supported by proof and trust signals",
        "reference_patterns": ["clear hero promise", "proof near CTA", "service cards", "simple contact capture"],
        "sections": ["hero", "services", "proof", "FAQ", "contact"],
        "anti_patterns": ["copied branding", "generic stock-template feel", "cluttered multi-CTA hero"],
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


def _extract_research_facts(lead: dict) -> dict[str, Any]:
    """Read design-related fields from research_facts if available."""
    raw = lead.get("research_facts")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_design_sources(lead: dict) -> dict[str, Any]:
    """Resolve structured design sources from the local adapter."""
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


async def _build_variants(brief: str, logo_url: str, site_type: str) -> list[dict]:
    """Build up to 5 design variants in parallel."""
    # Use 3 agents for demos (faster), 5 for full sites
    directions = DESIGN_DIRECTIONS[:3] if site_type == "demo" else DESIGN_DIRECTIONS

    tasks = []
    for direction in directions:
        tasks.append(_build_one_variant(direction, brief, logo_url))

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


async def _build_one_variant(direction: dict, brief: str, logo_url: str) -> dict:
    """Build a single variant using Claude with the design direction."""
    logo_line = f"\nLogo URL (use in the header): {logo_url}" if logo_url else ""

    prompt = f"""Build a complete, production-ready landing page as a single index.html file.

DESIGN DIRECTION: {direction['name']}
Style: {direction['style']}
Colors: {direction['colors']}
Fonts: {direction['fonts']}
Layout: {direction['layout']}
Animation: {direction['animation']}
Copy angle: {direction['copy_angle']}
{logo_line}

{brief}

TECHNICAL REQUIREMENTS:
- Single index.html file with Tailwind CSS CDN + Google Fonts
- Use a shadcn/ui-inspired design system expressed in semantic HTML + Tailwind utilities
- Adapt 21st.dev-style block patterns only when they fit the business and remain original
- Use Stitch-like art direction exploration for bold visual hierarchy, but ship original code
- Inline JavaScript for scroll animations via Intersection Observer
- Mobile responsive with working hamburger menu
- Accessible: WCAG 2.1 AA contrast, semantic HTML, alt text
- Reduced motion support via prefers-reduced-motion
- Fast: no heavy frameworks, lazy load images
- Use Unsplash for placeholder images (search terms related to {direction['name']})
- Never copy third-party branding, layouts, or markup directly

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
5. Overall conversion potential

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


async def _build_final(synthesis: dict, brief: str, logo_url: str) -> str:
    """Build the final page using Ralph Loop — retry until QA checks pass."""
    from shared.execution_loop import Step, TaskPlan, execute_plan

    cherry = synthesis.get("cherry_pick", {})
    logo_line = f"\nLogo URL: {logo_url}" if logo_url else ""

    build_prompt = f"""Build the FINAL production landing page combining the best elements.

{brief}
{logo_line}

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
- Single index.html with Tailwind CSS CDN + Google Fonts
- Use a shadcn/ui-inspired component system translated into original HTML/Tailwind
- Borrow 21st.dev-style section ideas only as adapted patterns, never copied code
- Preserve the strongest Stitch-like visual direction from the synthesis plan
- Inline JS for scroll animations (Intersection Observer)
- Mobile responsive with hamburger menu
- WCAG 2.1 AA accessible
- prefers-reduced-motion support
- Lazy load images
- No direct copying of markup, branding, or images from references

Write the COMPLETE HTML. Start with <!DOCTYPE html> and end with </html>.
Output ONLY the HTML code."""

    async def check_html_quality(result: str) -> dict:
        html = _extract_html(result)
        if not html:
            return {"passed": False, "error": "No valid HTML found in output"}
        issues = []
        if len(html) < 3000:
            issues.append("HTML too short — likely incomplete page")
        if "<nav" not in html.lower():
            issues.append("Missing navigation/header")
        if "tailwindcss" not in html.lower() and "tailwind" not in html.lower():
            issues.append("Missing Tailwind CSS CDN")
        if "<form" not in html.lower() and "contact" not in html.lower():
            issues.append("Missing contact form or CTA")
        if "prefers-reduced-motion" not in html:
            issues.append("Missing reduced-motion media query")
        if "<footer" not in html.lower():
            issues.append("Missing footer section")
        if issues:
            return {"passed": False, "error": "; ".join(issues)}
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
