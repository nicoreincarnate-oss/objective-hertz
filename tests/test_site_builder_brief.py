"""Tests for AI-led site planning briefs."""

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock


def run(coro):
    return asyncio.run(coro)


def load_site_builder_module(*, llm_result: str = "", skill_result: str = "", skill_present: bool = False):
    fake_comms = types.ModuleType("shared.comms")
    fake_comms.record_decision = AsyncMock()

    fake_config = types.ModuleType("shared.config")
    fake_config.config = SimpleNamespace(root_dir="/tmp/objective-hertz")

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_val = AsyncMock(return_value=0)

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = SimpleNamespace(generate=AsyncMock(return_value=llm_result))

    fake_skills = types.ModuleType("shared.skill_loader")
    fake_skills.find_skill = lambda name: "/tmp/ui-ux-pro-max/SKILL.md" if skill_present and name == "ui-ux-pro-max" else None
    fake_skills.execute_skill = AsyncMock(return_value=skill_result)

    sys.modules.pop("clawdbot.site_builder", None)
    sys.modules["shared.comms"] = fake_comms
    sys.modules["shared.config"] = fake_config
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.skill_loader"] = fake_skills

    module = importlib.import_module("clawdbot.site_builder")
    return module, fake_llm.llm, fake_comms, fake_skills


def test_extract_reference_urls_combines_lead_fields_and_research_urls():
    site_builder, _, _, _ = load_site_builder_module()

    lead = {
        "research_summary": (
            "Premium examples: https://alpha.example/work and "
            "https://beta.example. Repeat https://alpha.example/work"
        ),
        "reference_urls": ["https://gamma.example", "https://beta.example"],
        "reference_sites": [
            {"url": "https://delta.example", "note": "good CTA"},
            "https://epsilon.example",
        ],
    }

    assert site_builder._extract_reference_urls(lead) == [
        "https://gamma.example",
        "https://beta.example",
        "https://delta.example",
        "https://epsilon.example",
        "https://alpha.example/work",
    ]


def test_generate_reference_strategy_prompt_uses_references_and_copy_guardrails():
    llm_result = """
    {
      "market_position": "premium local services",
      "audience": "owners needing credibility fast",
      "visual_direction": "editorial trust with strong proof",
      "copy_angle": "authority without hype",
      "conversion_strategy": "book estimate CTA",
      "reference_patterns": ["split hero", "stacked proof"],
      "sections": ["hero", "services", "proof", "contact"],
      "anti_patterns": ["do not copy branding"]
    }
    """
    site_builder, llm, comms, _ = load_site_builder_module(llm_result=llm_result)

    lead = {
        "id": 7,
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "research_summary": "Reference https://studio.example and strong trust cues.",
    }

    strategy = run(site_builder._generate_reference_strategy(lead, "demo", 1))

    assert strategy["market_position"] == "premium local services"

    prompt = llm.generate.await_args.kwargs["prompt"]
    assert "https://studio.example" in prompt
    assert "Do NOT copy exact HTML" in prompt
    assert "Extract reusable patterns only" in prompt

    comms.record_decision.assert_awaited_once()


def test_build_product_brief_includes_strategy_and_no_copy_guardrail():
    strategy = {
        "market_position": "high-trust premium clinic",
        "audience": "families comparing clinics",
        "visual_direction": "clean editorial luxury",
        "copy_angle": "clarity and reassurance",
        "conversion_strategy": "primary CTA for appointment requests",
        "reference_patterns": ["minimal hero", "proof near CTA"],
        "sections": ["hero", "services", "testimonials", "contact"],
        "anti_patterns": ["no copied branding", "no cloned layouts"],
    }
    site_builder, _, _, _ = load_site_builder_module()
    site_builder._generate_reference_strategy = AsyncMock(return_value=strategy)

    lead = {
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "city": "Mazatlan",
        "country": "Mexico",
        "research_summary": "Family-friendly clinic with cosmetic services.",
    }

    brief = run(site_builder._build_product_brief(lead, "demo", 1))

    assert "REFERENCE STRATEGY" in brief
    assert "minimal hero" in brief
    assert "Do not copy exact HTML, copy, branding, images, or layouts." in brief
    assert "Build mode" in brief
    assert "Never use Unsplash" in brief


def test_generate_reference_strategy_includes_ui_skill_guidance_when_available():
    llm_result = """
    {
      "market_position": "premium local services",
      "audience": "owners needing credibility fast",
      "visual_direction": "editorial trust with strong proof",
      "copy_angle": "authority without hype",
      "conversion_strategy": "book estimate CTA",
      "reference_patterns": ["split hero"],
      "sections": ["hero", "proof", "contact"],
      "anti_patterns": ["generic stock look"]
    }
    """
    skill_result = """
    {
      "style_direction": "editorial premium trust",
      "palette": "warm neutrals and deep ink",
      "typography": "serif display + clean sans",
      "ux_notes": ["proof near CTA", "high contrast actions"]
    }
    """
    site_builder, llm, _, fake_skills = load_site_builder_module(
        llm_result=llm_result,
        skill_result=skill_result,
        skill_present=True,
    )

    lead = {
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "research_summary": "Reference https://studio.example and strong trust cues.",
    }

    strategy = run(site_builder._generate_reference_strategy(lead, "demo", 1))

    assert strategy["skill_guidance"]["style_direction"] == "editorial premium trust"
    prompt = llm.generate.await_args.kwargs["prompt"]
    assert "ui-ux-pro-max" in prompt
    assert "editorial premium trust" in prompt
    fake_skills.execute_skill.assert_awaited_once()


def test_generate_reference_strategy_includes_structured_design_sources():
    llm_result = """
    {
      "market_position": "premium local services",
      "audience": "owners needing credibility fast",
      "visual_direction": "editorial trust with strong proof",
      "copy_angle": "authority without hype",
      "conversion_strategy": "book estimate CTA",
      "reference_patterns": ["split hero"],
      "sections": ["hero", "proof", "contact"],
      "anti_patterns": ["generic stock look"]
    }
    """
    site_builder, llm, _, _ = load_site_builder_module(llm_result=llm_result)
    site_builder._resolve_design_sources = lambda lead: {
        "sources": [
            {"source": "21st.dev", "title": "Split Hero CTA", "why": "strong hero"},
            {"source": "stitch", "title": "Premium Service Editorial", "why": "clear art direction"},
        ],
        "adaptation_rules": ["adapt blocks, do not clone"],
    }

    lead = {
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "research_summary": "Reference https://studio.example and strong trust cues.",
    }

    strategy = run(site_builder._generate_reference_strategy(lead, "demo", 1))

    assert strategy["design_sources"][0]["source"] == "21st.dev"
    prompt = llm.generate.await_args.kwargs["prompt"]
    assert "Split Hero CTA" in prompt
    assert "Premium Service Editorial" in prompt


def test_resolve_build_plan_disables_paid_assets_when_budget_is_exhausted():
    site_builder, _, _, _ = load_site_builder_module()

    strategy = {
        "visual_direction": "clean trust",
        "reference_patterns": [],
        "design_sources": [],
        "governing_idea": "memorable service clarity",
        "emotional_target": "trust fast",
    }
    site_builder._safe_fetch_val = AsyncMock(side_effect=[30.0, 0.0])

    plan = run(
        site_builder._resolve_build_plan(
            {"id": 7, "business_name": "Atlas Dental", "industry": "dentist"},
            site_type="demo",
            page_count=1,
            strategy=strategy,
        )
    )

    assert plan["allow_paid_assets"] is False
    assert plan["remaining_monthly_asset_budget"] == 0.0


def test_build_one_variant_prompt_includes_runtime_hints_and_cdns():
    html = "<!DOCTYPE html><html><body><nav></nav><h1>Atlas Dental</h1><footer></footer></body></html>"
    site_builder, llm, _, _ = load_site_builder_module(llm_result=html)

    direction = next(item for item in site_builder.DESIGN_DIRECTIONS if item["name"] == "immersive-3d")

    run(
        site_builder._build_one_variant(
            direction,
            "PRODUCT BRIEF",
            {"logo_url": "", "hero_url": ""},
            {"runtime_profile": "gsap-lenis"},
        )
    )

    prompt = llm.generate.await_args.args[0]
    assert "You may include these runtime libraries via CDN script tags" in prompt
    assert "three.min.js" in prompt
    assert "ScrollTrigger.min.js" in prompt
    assert "All interactive elements must have non-JS fallbacks" in prompt


def test_resolve_authored_assets_normalizes_optional_rive_spline_and_theatre_inputs():
    site_builder, _, _, _ = load_site_builder_module()

    lead = {
        "rive_asset": {"url": "https://cdn.example/hero.riv", "usage": "hero accent"},
        "spline_scene": "https://prod.spline.design/example/scene.splinecode",
        "theatre_notes": [{"name": "hero camera", "notes": "Slow reveal on scroll"}],
    }

    resolved = site_builder._resolve_authored_assets(lead, {})

    assert resolved["has_authored_assets"] is True
    assert resolved["rive_assets"][0]["url"] == "https://cdn.example/hero.riv"
    assert resolved["spline_assets"][0]["url"] == "https://prod.spline.design/example/scene.splinecode"
    assert resolved["theatre_sequences"][0]["name"] == "hero camera"


def test_build_one_variant_prompt_includes_authored_asset_guidance_when_available():
    html = "<!DOCTYPE html><html><body><nav></nav><h1>Atlas Dental</h1><footer></footer></body></html>"
    site_builder, llm, _, _ = load_site_builder_module(llm_result=html)

    direction = next(item for item in site_builder.DESIGN_DIRECTIONS if item["name"] == "immersive-3d")
    asset_pack = {
        "logo_url": "",
        "hero_url": "",
        "rive_assets": [{"url": "https://cdn.example/micro.riv", "usage": "icon motion"}],
        "spline_assets": [{"url": "https://prod.spline.design/example/scene.splinecode", "usage": "hero scene"}],
        "theatre_sequences": [{"name": "hero reveal", "notes": "Slow camera drift on scroll"}],
    }

    run(
        site_builder._build_one_variant(
            direction,
            "PRODUCT BRIEF",
            asset_pack,
            {"runtime_profile": "gsap-lenis"},
        )
    )

    prompt = llm.generate.await_args.args[0]
    assert "AUTHORED ASSETS AVAILABLE" in prompt
    assert "https://cdn.example/micro.riv" in prompt
    assert "https://prod.spline.design/example/scene.splinecode" in prompt
    assert "Theatre.js sequence notes" in prompt
    assert "translate the timing into GSAP/Three behavior instead" in prompt


# ── Multi-page tests ────────────────────────────────────────────────


def test_extract_shared_shell_captures_nav_footer_head():
    site_builder, _, _, _ = load_site_builder_module()

    index_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Atlas Dental</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>:root { --ink: #101828; --accent: #e85d50; }</style>
</head>
<body>
<nav class="flex items-center justify-between px-6 py-4">
  <a href="index.html">Atlas Dental</a>
  <div><a href="about.html">About</a><a href="services.html">Services</a><a href="contact.html">Contact</a></div>
</nav>
<main><h1>Welcome</h1><p>Best dentist in Mazatlan.</p></main>
<footer class="bg-gray-900 text-white py-8"><p>© Atlas Dental</p></footer>
</body>
</html>"""

    shell = site_builder._extract_shared_shell(index_html)

    assert "<nav" in shell
    assert "<footer" in shell
    assert "<head" in shell
    assert "--ink" in shell
    assert "tailwindcss" in shell


def test_build_page_brief_uses_lead_data():
    site_builder, _, _, _ = load_site_builder_module()

    lead = {
        "business_name": "Atlas Dental",
        "industry": "dentist",
        "city": "Mazatlan",
        "research_summary": "Family-friendly clinic with cosmetic services and 15 years experience.",
        "email": "hello@atlasdental.mx",
    }

    about_brief = site_builder._build_page_brief("about.html", lead)
    assert "Atlas Dental" in about_brief
    assert "About" in about_brief
    assert "Family-friendly" in about_brief

    contact_brief = site_builder._build_page_brief("contact.html", lead)
    assert "hello@atlasdental.mx" in contact_brief
    assert "Mazatlan" in contact_brief


def test_build_one_variant_prompt_multipage_includes_nav_links():
    html = "<!DOCTYPE html><html><body><nav></nav><h1>Atlas Dental</h1><footer></footer></body></html>"
    site_builder, llm, _, _ = load_site_builder_module(llm_result=html)

    direction = next(item for item in site_builder.DESIGN_DIRECTIONS if item["name"] == "minimal-geometric")

    run(
        site_builder._build_one_variant(
            direction,
            "PRODUCT BRIEF",
            {"logo_url": "", "hero_url": ""},
            {"runtime_profile": "gsap-lenis", "page_count": 5},
        )
    )

    prompt = llm.generate.await_args.args[0]
    assert "HOME PAGE" in prompt
    assert "about.html" in prompt
    assert "services.html" in prompt
    assert "contact.html" in prompt


def test_build_one_variant_prompt_demo_no_nav_links():
    html = "<!DOCTYPE html><html><body><nav></nav><h1>Atlas Dental</h1><footer></footer></body></html>"
    site_builder, llm, _, _ = load_site_builder_module(llm_result=html)

    direction = next(item for item in site_builder.DESIGN_DIRECTIONS if item["name"] == "minimal-geometric")

    run(
        site_builder._build_one_variant(
            direction,
            "PRODUCT BRIEF",
            {"logo_url": "", "hero_url": ""},
            {"runtime_profile": "gsap-lenis", "page_count": 1},
        )
    )

    prompt = llm.generate.await_args.args[0]
    assert "landing page" in prompt.lower()
    assert "about.html" not in prompt


def test_minimal_fallback_page_contains_shared_elements():
    site_builder, _, _, _ = load_site_builder_module()

    shell = """<!-- SHARED HEAD -->
<head><title>Test</title><script src="https://cdn.tailwindcss.com"></script></head>
<!-- SHARED NAV -->
<nav><a href="index.html">Home</a></nav>
<!-- SHARED FOOTER -->
<footer><p>© Test</p></footer>"""

    lead = {"business_name": "Atlas Dental", "industry": "dentist"}
    fallback = site_builder._minimal_fallback_page(shell, "About", lead)

    assert "<!DOCTYPE html>" in fallback
    assert "<nav>" in fallback or "<nav " in fallback
    assert "<footer>" in fallback or "<footer " in fallback
    assert "About" in fallback
    assert "Atlas Dental" in fallback


def test_inner_page_specs_cover_all_expected_pages():
    site_builder, _, _, _ = load_site_builder_module()

    assert "about.html" in site_builder.INNER_PAGE_SPECS
    assert "services.html" in site_builder.INNER_PAGE_SPECS
    assert "gallery.html" in site_builder.INNER_PAGE_SPECS
    assert "contact.html" in site_builder.INNER_PAGE_SPECS
    assert len(site_builder.INNER_PAGE_SPECS) == 4
