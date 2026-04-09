"""Page assembly engine for ClawdBot v2.

Composes individually generated sections into complete HTML pages with
SEO meta tags, Schema.org JSON-LD, script coordination (GSAP, Alpine,
effects), and multi-page site generation.
"""

from __future__ import annotations

import html
import json
import logging
import textwrap
from typing import Any

try:
    from clawdbot.section_agent import SectionResult
except ImportError:
    SectionResult = None  # type: ignore[assignment,misc]

try:
    from clawdbot.section_planner import BuildPlan, SectionPlan
except ImportError:
    BuildPlan = None  # type: ignore[assignment,misc]
    SectionPlan = None  # type: ignore[assignment,misc]

try:
    from clawdbot.effects import get_effect_css, inject_effect
except ImportError:
    def get_effect_css(name: str) -> str:  # type: ignore[misc]
        return ""

    def inject_effect(name: str, sel: str, cfg: dict | None = None) -> str:  # type: ignore[misc]
        return ""

logger = logging.getLogger("perseus.clawdbot.page_assembler")


# ---------------------------------------------------------------------------
# SEO helpers
# ---------------------------------------------------------------------------


def _build_seo_meta(business_context: dict[str, Any], site_type: str) -> str:
    """Build SEO meta tags: title, description, OG, Twitter, canonical, robots."""
    name = business_context.get("name", "Business")
    industry = business_context.get("industry", "")
    tagline = business_context.get("tagline", "")
    description = business_context.get("description", "")
    address = business_context.get("address", "")

    # Title: business name + primary keyword + location (under 60 chars)
    location = _extract_city(address)
    title_parts = [name]
    if industry:
        title_parts.append(industry.title())
    if location:
        title_parts.append(location)
    title = " | ".join(title_parts)
    if len(title) > 60:
        title = title[:57] + "..."

    # Description: specific value prop (under 155 chars)
    meta_desc = tagline or description or f"{name} - Professional {industry} services"
    if len(meta_desc) > 155:
        meta_desc = meta_desc[:152] + "..."

    canonical = f"https://www.{_slugify(name)}.com"

    lines = [
        f'<title>{html.escape(title)}</title>',
        f'<meta name="description" content="{html.escape(meta_desc)}">',
        '<meta name="robots" content="index, follow">',
        f'<link rel="canonical" href="{html.escape(canonical)}">',
        "",
        "<!-- Open Graph -->",
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(meta_desc)}">',
        f'<meta property="og:url" content="{html.escape(canonical)}">',
        f'<meta property="og:site_name" content="{html.escape(name)}">',
        "",
        "<!-- Twitter Card -->",
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{html.escape(title)}">',
        f'<meta name="twitter:description" content="{html.escape(meta_desc)}">',
    ]
    return "\n    ".join(lines)


def _build_schema_org(business_context: dict[str, Any]) -> str:
    """Build Schema.org LocalBusiness JSON-LD."""
    name = business_context.get("name", "Business")
    description = business_context.get("description", "")
    industry = business_context.get("industry", "")
    phone = business_context.get("phone", "")
    email = business_context.get("email", "")
    address = business_context.get("address", "")
    services = business_context.get("services", [])

    service_names: list[str] = []
    if isinstance(services, list):
        for s in services:
            if isinstance(s, str):
                service_names.append(s)
            elif isinstance(s, dict):
                service_names.append(s.get("name", ""))

    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": name,
        "description": description or f"Professional {industry} services",
    }

    if address:
        schema["address"] = {
            "@type": "PostalAddress",
            "streetAddress": address,
        }

    if phone:
        schema["telephone"] = phone
    if email:
        schema["email"] = email
    if service_names:
        schema["hasOfferCatalog"] = {
            "@type": "OfferCatalog",
            "name": "Services",
            "itemListElement": [
                {"@type": "Offer", "itemOffered": {"@type": "Service", "name": sn}}
                for sn in service_names
                if sn
            ],
        }

    json_str = json.dumps(schema, indent=2, ensure_ascii=False)
    return f'<script type="application/ld+json">\n{json_str}\n</script>'


def _build_gsap_init(sections: dict[str, Any], build_plan: Any) -> str:
    """Build GSAP ScrollTrigger initialization script."""
    has_gsap = False
    if build_plan is not None:
        contract = getattr(build_plan, "design_contract", {})
        motion = contract.get("motion", {})
        has_gsap = bool(motion.get("scroll_trigger", False))

    if not has_gsap:
        # Check if any section HTML references gsap or ScrollTrigger
        all_html = " ".join(
            getattr(sr, "html", sr) if not isinstance(sr, str) else sr
            for sr in sections.values()
        )
        if "gsap" not in all_html.lower() and "scrolltrigger" not in all_html.lower():
            return ""

    return textwrap.dedent("""\
        <script>
        document.addEventListener('DOMContentLoaded', function() {
          if (typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined') {
            gsap.registerPlugin(ScrollTrigger);
            ScrollTrigger.refresh();
            // Batch animate elements with data-animate
            gsap.utils.toArray('[data-animate]').forEach(function(el) {
              gsap.from(el, {
                y: 30,
                opacity: 0,
                duration: 0.6,
                ease: 'power2.out',
                scrollTrigger: {
                  trigger: el,
                  start: 'top 85%',
                  toggleActions: 'play none none none'
                }
              });
            });
          }
        });
        </script>""")


def _build_effects_init(sections: dict[str, Any], build_plan: Any) -> str:
    """Collect effect CSS and init scripts for sections that use effects."""
    if build_plan is None:
        return ""

    contract = getattr(build_plan, "design_contract", {})
    motion = contract.get("motion", {})
    if not motion.get("scroll_trigger", False):
        return ""

    effect_blocks: list[str] = []
    seen_effects: set[str] = set()

    for sp in getattr(build_plan, "sections", []):
        content = getattr(sp, "content", {})
        effects = content.get("effects", [])
        if isinstance(effects, list):
            for eff in effects:
                eff_name = eff.get("name", "") if isinstance(eff, dict) else str(eff)
                if eff_name and eff_name not in seen_effects:
                    seen_effects.add(eff_name)
                    css = get_effect_css(eff_name)
                    if css:
                        effect_blocks.append(css)

    return "\n".join(effect_blocks)


def _build_body_classes(direction_name: str) -> str:
    """Generate body CSS classes based on design direction."""
    base = "antialiased min-h-screen"
    direction_classes: dict[str, str] = {
        "dark-cinematic": "bg-black text-white",
        "cosmos-dark-curation": "bg-neutral-950 text-neutral-100",
        "arena-monastic-grid": "bg-white text-neutral-900",
        "minimal-geometric": "bg-white text-neutral-900",
        "bold-editorial": "bg-amber-50 text-slate-800",
        "organic-illustrated": "bg-stone-50 text-stone-900",
        "playful-animated": "bg-white text-gray-900",
        "immersive-3d": "bg-black text-white",
        "yokoo-psychedelic-maximalism": "bg-white text-black",
        "fukuda-optical-precision": "bg-white text-black",
    }
    extra = direction_classes.get(direction_name, "")
    return f"{base} {extra}".strip()


def _extract_city(address: str) -> str:
    """Extract city name from an address string."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        return parts[-2] if len(parts) >= 3 else parts[0]
    return ""


def _slugify(text: str) -> str:
    """Simple slugify for URL generation."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-")


def _get_section_html(section: Any) -> str:
    """Extract HTML string from a SectionResult or plain string."""
    if isinstance(section, str):
        return section
    return getattr(section, "html", "")


# ---------------------------------------------------------------------------
# Task 1: Page Assembler
# ---------------------------------------------------------------------------


async def assemble_page(
    sections: dict[str, Any],
    build_plan: Any,
) -> str:
    """Assemble section fragments into a complete HTML page.

    1. Build <head> from design tokens (fonts, Tailwind, CSS vars, meta, schema.org)
    2. Order sections according to build plan
    3. Inject shared scripts (GSAP init, Alpine, effects)
    4. Add nav + footer wrappers
    5. Add responsive meta tags
    6. Validate assembled HTML

    Args:
        sections: Dict mapping section_type to SectionResult or HTML string.
        build_plan: BuildPlan with design_contract, sections, business_context.

    Returns:
        Complete HTML page string.
    """
    contract = getattr(build_plan, "design_contract", {})
    business_context = getattr(build_plan, "business_context", {})
    direction_name = getattr(build_plan, "direction_name", "")
    site_type = getattr(build_plan, "site_type", "demo")

    # Head block from design contract (includes fonts, Tailwind, CSS vars)
    head_block = contract.get("head_block", "")

    # SEO meta
    seo_meta = _build_seo_meta(business_context, site_type)

    # Schema.org JSON-LD
    schema_org = _build_schema_org(business_context)

    # Body classes
    body_classes = _build_body_classes(direction_name)

    # Order sections from build_plan
    plan_sections = getattr(build_plan, "sections", [])
    ordered_types = [getattr(sp, "section_type", "") for sp in plan_sections]

    # Extract navbar and footer
    navbar_html = _get_section_html(sections.get("navbar", ""))
    footer_html = _get_section_html(sections.get("footer", ""))

    # Main content sections (in plan order, excluding navbar/footer)
    main_sections: list[str] = []
    for stype in ordered_types:
        if stype in ("navbar", "footer"):
            continue
        section_data = sections.get(stype)
        if section_data:
            section_html = _get_section_html(section_data)
            if section_html.strip():
                main_sections.append(section_html)

    main_content = "\n    ".join(main_sections)

    # Scripts
    gsap_init = _build_gsap_init(sections, build_plan)
    effects_init = _build_effects_init(sections, build_plan)

    # Reduced motion style
    reduced_motion_css = textwrap.dedent("""\
        <style>
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
            scroll-behavior: auto !important;
          }
        }
        </style>""")

    page = textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            {head_block}
            {seo_meta}
            {schema_org}
            {reduced_motion_css}
        </head>
        <body class="{body_classes}">
            {navbar_html}
            <main>
                {main_content}
            </main>
            {footer_html}
            {gsap_init}
            {effects_init}
        </body>
        </html>""")

    return page


# ---------------------------------------------------------------------------
# Task 2: Multi-page Assembly
# ---------------------------------------------------------------------------

# Inner page definitions: page_name -> (title_suffix, sections_to_generate)
_INNER_PAGES: dict[str, dict[str, Any]] = {
    "about.html": {
        "title_suffix": "About Us",
        "content_prompt": (
            "Generate an About Us page section for {name}, a {industry} business. "
            "Include: company story (2-3 paragraphs), mission statement, "
            "team overview. Use real-sounding content, not placeholder text. "
            "Output ONLY the <section>...</section> HTML with Tailwind classes."
        ),
    },
    "services.html": {
        "title_suffix": "Our Services",
        "content_prompt": (
            "Generate a detailed services page for {name}, a {industry} business. "
            "Expand on these services: {services}. Each service should have a "
            "heading, 2-3 sentence description, and a subtle icon placeholder using "
            "inline SVG. Output ONLY <section>...</section> HTML with Tailwind."
        ),
    },
    "gallery.html": {
        "title_suffix": "Gallery",
        "content_prompt": (
            "Generate a gallery/portfolio page section for {name}, a {industry} business. "
            "Create a responsive grid with 6-8 items. Each item uses a colored "
            "CSS gradient as visual placeholder (no image URLs). Include hover "
            "effects. Output ONLY <section>...</section> HTML with Tailwind."
        ),
    },
    "contact.html": {
        "title_suffix": "Contact Us",
        "content_prompt": (
            "Generate a contact page section for {name}, a {industry} business. "
            "Include: a contact form (name, email, phone, message), business "
            "contact details ({phone}, {email}, {address}), and business hours. "
            "Output ONLY <section>...</section> HTML with Tailwind."
        ),
    },
}


async def assemble_multipage_site(
    home_sections: dict[str, Any],
    build_plan: Any,
) -> dict[str, str]:
    """Generate a multi-page site with consistent design system.

    Strategy:
    - Nav + footer: identical across all pages (from home generation)
    - Each inner page gets a minimal content section generated via LLM (Haiku)
    - All pages share same <head>, CSS vars, design tokens
    - Nav links point to correct filenames

    Args:
        home_sections: Section results from the home page build.
        build_plan: BuildPlan with design contract and business context.

    Returns:
        Dict mapping filename to HTML string:
        {"index.html": "...", "about.html": "...", "services.html": "...",
         "gallery.html": "...", "contact.html": "..."}
    """
    pages: dict[str, str] = {}

    # 1. Assemble home page (index.html)
    pages["index.html"] = await assemble_page(home_sections, build_plan)

    # 2. Extract shared elements
    navbar_html = _get_section_html(home_sections.get("navbar", ""))
    footer_html = _get_section_html(home_sections.get("footer", ""))

    # Ensure nav links point to filenames
    navbar_html = _update_nav_links(navbar_html)

    business_context = getattr(build_plan, "business_context", {})
    contract = getattr(build_plan, "design_contract", {})
    direction_name = getattr(build_plan, "direction_name", "")
    site_type = getattr(build_plan, "site_type", "demo")

    # 3. Generate inner pages
    for filename, page_def in _INNER_PAGES.items():
        try:
            inner_html = await _generate_inner_page_content(
                page_def, business_context,
            )
        except Exception:
            logger.warning("Failed to generate %s; using fallback", filename, exc_info=True)
            title_suffix = page_def["title_suffix"]
            inner_html = (
                f'<section class="py-20 px-6 max-w-4xl mx-auto">'
                f'<h1 class="text-4xl font-bold mb-8">{html.escape(title_suffix)}</h1>'
                f'<p class="text-lg text-gray-600">Content for {html.escape(title_suffix)} page.</p>'
                f'</section>'
            )

        # Build inner page context with modified title
        inner_biz = dict(business_context)
        inner_biz["tagline"] = page_def["title_suffix"]

        head_block = contract.get("head_block", "")
        seo_meta = _build_seo_meta(inner_biz, site_type)
        body_classes = _build_body_classes(direction_name)
        gsap_init = _build_gsap_init(home_sections, build_plan)

        reduced_motion_css = textwrap.dedent("""\
            <style>
            @media (prefers-reduced-motion: reduce) {
              *, *::before, *::after {
                animation-duration: 0.01ms !important;
                animation-iteration-count: 1 !important;
                transition-duration: 0.01ms !important;
                scroll-behavior: auto !important;
              }
            }
            </style>""")

        page = textwrap.dedent(f"""\
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                {head_block}
                {seo_meta}
                {reduced_motion_css}
            </head>
            <body class="{body_classes}">
                {navbar_html}
                <main>
                    {inner_html}
                </main>
                {footer_html}
                {gsap_init}
            </body>
            </html>""")

        pages[filename] = page

    return pages


async def _generate_inner_page_content(
    page_def: dict[str, Any],
    business_context: dict[str, Any],
) -> str:
    """Generate inner page content via LLM (Haiku/fast tier for cost)."""
    name = business_context.get("name", "Business")
    industry = business_context.get("industry", "business")
    services = business_context.get("services", [])
    phone = business_context.get("phone", "")
    email = business_context.get("email", "")
    address = business_context.get("address", "")

    services_str = ""
    if isinstance(services, list):
        svc_names = []
        for s in services:
            if isinstance(s, str):
                svc_names.append(s)
            elif isinstance(s, dict):
                svc_names.append(s.get("name", ""))
        services_str = ", ".join(svc_names) if svc_names else "general services"

    prompt = page_def["content_prompt"].format(
        name=name,
        industry=industry,
        services=services_str,
        phone=phone,
        email=email,
        address=address,
    )

    try:
        from shared.llm_client import llm
        raw = await llm.generate(
            prompt,
            model="fast",
            max_tokens=2048,
            temperature=0.5,
            pipeline_stage="page_assembler_inner_page",
            operation="clawdbot.assemble_inner_page",
            daemon_name="clawdbot",
        )
        # Extract section HTML
        from clawdbot.section_agent import extract_section_html
        extracted = extract_section_html(raw)
        if extracted:
            return extracted
        return raw.strip()
    except ImportError:
        logger.warning("LLM client not available for inner page generation")
        title = page_def["title_suffix"]
        return (
            f'<section class="py-20 px-6 max-w-4xl mx-auto">'
            f'<h1 class="text-4xl font-bold mb-8">{html.escape(title)}</h1>'
            f'<p class="text-lg">Professional {html.escape(industry)} services by {html.escape(name)}.</p>'
            f'</section>'
        )


def _update_nav_links(navbar_html: str) -> str:
    """Update nav anchor hrefs to point to correct filenames."""
    # Map section anchors to page files
    link_map = {
        "#about": "about.html",
        "#services": "services.html",
        "#gallery": "gallery.html",
        "#contact": "contact.html",
        "#portfolio": "gallery.html",
    }
    result = navbar_html
    for anchor, filename in link_map.items():
        result = result.replace(f'href="{anchor}"', f'href="{filename}"')
    return result
