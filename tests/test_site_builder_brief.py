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
