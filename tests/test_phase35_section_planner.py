"""Tests for Phase 35: Section Decomposition + Build Planning."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawdbot.design_tokens import DesignTokens
from clawdbot.section_planner import (
    DEMO_TIER,
    INDUSTRY_SECTION_TEMPLATES,
    PREMIUM_TIER,
    BuildPlan,
    SectionPlan,
    decompose_reference_site,
    extract_section_content,
    generate_build_plan,
    generate_design_contract,
    get_section_template,
    select_build_tier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_lead() -> dict:
    return {
        "business_name": "Bright Smile Dental",
        "industry": "dentist",
        "tagline": "Your smile, our passion",
        "description": "Family dentistry in downtown Portland",
        "phone": "503-555-0123",
        "email": "hello@brightsmile.com",
        "address": "123 Main St, Portland, OR 97201",
        "services": [
            {"name": "Teeth Whitening", "description": "Professional whitening in one visit"},
            {"name": "Dental Implants", "description": "Permanent tooth replacement"},
            {"name": "Routine Checkups", "description": "Preventive care for the whole family"},
        ],
        "testimonials": [
            {"text": "Best dentist ever!", "client_name": "Sarah M.", "rating": 5},
            {"text": "Painless experience.", "client_name": "John D.", "rating": 5},
        ],
    }


@pytest.fixture
def sample_tokens() -> DesignTokens:
    return DesignTokens(
        primary="#1a1a2e",
        secondary="#e8e0d5",
        accent="#e94560",
        background="#0a0a14",
        surface="#141422",
        text_primary="#e8e0d5",
        text_secondary="#a0a0b0",
        text_muted="#606070",
        font_display="Syne",
        font_body="DM Sans",
        direction_name="cosmos-dark-curation",
    )


@pytest.fixture
def sample_business_context() -> dict:
    return {
        "name": "Bright Smile Dental",
        "industry": "dentist",
        "tagline": "Your smile, our passion",
        "description": "Family dentistry",
        "services": [],
        "phone": "503-555-0123",
        "email": "hello@brightsmile.com",
        "address": "123 Main St",
    }


# ---------------------------------------------------------------------------
# Task 1: Build plan generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_plan_generation(sample_lead: dict) -> None:
    """generate_build_plan produces a BuildPlan with correct sections."""
    with patch("clawdbot.section_planner.load_taste_profile", return_value={}), \
         patch("clawdbot.section_planner.select_snippets_for_build", return_value={}):
        plan = await generate_build_plan(sample_lead, site_type="demo", page_count=1)

    assert isinstance(plan, BuildPlan)
    assert plan.site_type == "demo"
    assert plan.page_count == 1
    assert plan.total_sections == len(plan.sections)
    assert plan.total_sections > 0
    assert plan.direction_name  # non-empty
    assert plan.estimated_build_time_s > 0
    assert plan.tier == DEMO_TIER

    # Check sections are SectionPlan instances with sequential ordering
    for i, sp in enumerate(plan.sections):
        assert isinstance(sp, SectionPlan)
        assert sp.order == i
        assert sp.section_type


@pytest.mark.asyncio
async def test_build_plan_premium(sample_lead: dict) -> None:
    """Premium plan uses PREMIUM_TIER and allows more sections."""
    with patch("clawdbot.section_planner.load_taste_profile", return_value={}), \
         patch("clawdbot.section_planner.select_snippets_for_build", return_value={}):
        plan = await generate_build_plan(sample_lead, site_type="full", page_count=5)

    assert plan.tier == PREMIUM_TIER
    assert plan.site_type == "full"


# ---------------------------------------------------------------------------
# Task 2: Design contract
# ---------------------------------------------------------------------------


def test_design_contract(sample_tokens: DesignTokens, sample_business_context: dict) -> None:
    """Design contract includes all required fields."""
    section_list = ["hero", "services", "testimonials", "contact", "footer"]
    contract = generate_design_contract(sample_tokens, sample_business_context, section_list)

    # Required top-level keys
    required_keys = [
        "palette", "typography", "spacing", "nav", "footer",
        "motion", "brand", "css_vars_block", "head_block", "tailwind_config",
    ]
    for key in required_keys:
        assert key in contract, f"Missing key: {key}"

    # Palette completeness
    palette_keys = ["primary", "secondary", "accent", "bg", "surface", "text", "text_secondary", "text_muted"]
    for pk in palette_keys:
        assert pk in contract["palette"], f"Missing palette key: {pk}"

    # Typography
    assert "heading_font" in contract["typography"]
    assert "body_font" in contract["typography"]
    assert "scale" in contract["typography"]

    # Brand
    assert contract["brand"]["name"] == "Bright Smile Dental"
    assert contract["brand"]["industry"] == "dentist"

    # CSS vars block is non-empty
    assert len(contract["css_vars_block"]) > 50
    assert len(contract["head_block"]) > 50


# ---------------------------------------------------------------------------
# Task 3: Content extraction
# ---------------------------------------------------------------------------


def test_content_extraction_hero(sample_lead: dict) -> None:
    """Hero section extracts headline and CTA from lead data."""
    content = extract_section_content(sample_lead, "hero")
    assert content["headline"] == "Your smile, our passion"
    assert content["business_name"] == "Bright Smile Dental"
    assert content["cta_text"] == "Get Started"


def test_content_extraction_services(sample_lead: dict) -> None:
    """Services section extracts from lead services list."""
    content = extract_section_content(sample_lead, "services")
    assert len(content["services"]) == 3
    assert content["services"][0]["name"] == "Teeth Whitening"


def test_content_extraction_testimonials(sample_lead: dict) -> None:
    """Testimonials section extracts from lead data."""
    content = extract_section_content(sample_lead, "testimonials")
    assert len(content["testimonials"]) == 2
    assert content["testimonials"][0]["client_name"] == "Sarah M."


def test_content_extraction_contact(sample_lead: dict) -> None:
    """Contact section extracts phone, email, address."""
    content = extract_section_content(sample_lead, "contact")
    assert content["phone"] == "503-555-0123"
    assert content["email"] == "hello@brightsmile.com"
    assert content["address"] == "123 Main St, Portland, OR 97201"


def test_content_extraction_navbar(sample_lead: dict) -> None:
    """Navbar section extracts business name."""
    content = extract_section_content(sample_lead, "navbar")
    assert content["business_name"] == "Bright Smile Dental"


def test_content_extraction_footer(sample_lead: dict) -> None:
    """Footer section extracts business info."""
    content = extract_section_content(sample_lead, "footer")
    assert content["business_name"] == "Bright Smile Dental"
    assert content["phone"] == "503-555-0123"


def test_content_extraction_all_types(sample_lead: dict) -> None:
    """All defined section types return content without error."""
    section_types = ["hero", "services", "features", "testimonials", "contact",
                     "pricing", "faq", "navbar", "footer", "badges", "cta"]
    for st in section_types:
        content = extract_section_content(sample_lead, st)
        assert isinstance(content, dict)


def test_content_extraction_missing_marks_generation(sample_lead: dict) -> None:
    """Missing data gets _missing marker for LLM generation."""
    empty_lead: dict = {"industry": "dentist"}
    content = extract_section_content(empty_lead, "services")
    assert "_missing" in content
    assert "services" in content["_missing"]


@pytest.mark.asyncio
async def test_content_extraction_missing_generates() -> None:
    """Missing data triggers LLM generation (mocked)."""
    import clawdbot.section_planner as sp

    content = {
        "_missing": ["services"],
        "_generation_context": {"industry": "dentist"},
    }

    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value=json.dumps({
        "services": [
            {"name": "Teeth Cleaning", "description": "Professional cleaning"},
            {"name": "Fillings", "description": "Cavity repair"},
        ]
    }))

    mock_module = MagicMock()
    mock_module.llm = mock_llm

    with patch.dict("sys.modules", {"shared.llm_client": mock_module}):
        result = await sp._fill_missing_content(dict(content), "services")

    assert "services" in result
    assert len(result["services"]) == 2
    assert "_missing" not in result


# ---------------------------------------------------------------------------
# Task 5: Build tier system
# ---------------------------------------------------------------------------


def test_build_tier_selection_demo() -> None:
    """demo site_type selects DEMO_TIER."""
    tier = select_build_tier("demo")
    assert tier is DEMO_TIER
    assert tier.name == "demo"
    assert tier.estimated_cost_usd == 0.00
    assert tier.max_sections == 4
    assert tier.max_iterations == 1
    assert tier.use_claude_final_qa is False


def test_build_tier_selection_full() -> None:
    """full site_type selects PREMIUM_TIER."""
    tier = select_build_tier("full")
    assert tier is PREMIUM_TIER
    assert tier.name == "premium"
    assert tier.estimated_cost_usd == 0.19
    assert tier.max_sections == 8
    assert tier.max_iterations == 3
    assert tier.use_claude_final_qa is True


def test_build_tier_selection_unknown() -> None:
    """Unknown site_type defaults to DEMO_TIER."""
    tier = select_build_tier("unknown")
    assert tier is DEMO_TIER


# ---------------------------------------------------------------------------
# Task 6: Industry section templates
# ---------------------------------------------------------------------------


def test_industry_templates_all_present() -> None:
    """All 7 industry templates are defined."""
    expected = {"dentist", "plumber", "restaurant", "saas", "legal", "real_estate", "default"}
    assert expected == set(INDUSTRY_SECTION_TEMPLATES.keys())


def test_industry_templates_valid_sections() -> None:
    """All templates return valid section lists."""
    for industry, sections in INDUSTRY_SECTION_TEMPLATES.items():
        assert len(sections) >= 4, f"{industry} has too few sections"
        assert "hero" in sections, f"{industry} missing hero"
        assert "footer" in sections, f"{industry} missing footer"


def test_industry_template_dentist() -> None:
    """Dentist template includes expected sections."""
    sections = INDUSTRY_SECTION_TEMPLATES["dentist"]
    assert "services" in sections
    assert "testimonials" in sections
    assert "faq" in sections


def test_tier_section_truncation() -> None:
    """Demo tier truncates to max_sections while keeping essentials."""
    # Dentist has 8 sections, demo tier allows 4
    sections = get_section_template("dentist", DEMO_TIER)
    assert len(sections) <= DEMO_TIER.max_sections
    # Must keep hero and footer
    assert "hero" in sections
    assert "footer" in sections


def test_tier_no_truncation_premium() -> None:
    """Premium tier keeps all sections."""
    sections = get_section_template("dentist", PREMIUM_TIER)
    assert len(sections) == len(INDUSTRY_SECTION_TEMPLATES["dentist"])


def test_section_template_default_fallback() -> None:
    """Unknown industry falls back to default template."""
    sections = get_section_template("unknown_industry", DEMO_TIER)
    assert len(sections) > 0
    assert "hero" in sections


# ---------------------------------------------------------------------------
# Task 4: VLM reference decomposition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_decomposition_mock() -> None:
    """decompose_reference_site returns section list from mocked VLM."""
    mock_vlm_response = [
        {"type": "hero", "y_start_pct": 0, "y_end_pct": 25,
         "description": "Hero with headline and CTA", "dominant_colors": ["#1a1a2e"]},
        {"type": "features", "y_start_pct": 25, "y_end_pct": 50,
         "description": "Three feature cards", "dominant_colors": ["#ffffff"]},
        {"type": "testimonials", "y_start_pct": 50, "y_end_pct": 75,
         "description": "Client quotes", "dominant_colors": ["#f5f5f5"]},
        {"type": "footer", "y_start_pct": 75, "y_end_pct": 100,
         "description": "Footer with links", "dominant_colors": ["#1a1a2e"]},
    ]

    mock_screenshot = AsyncMock(return_value=b"fake-png-bytes")
    mock_vlm = AsyncMock(return_value=mock_vlm_response)

    with patch("clawdbot.section_planner._capture_reference_screenshot", mock_screenshot), \
         patch("clawdbot.section_planner._vlm_score", mock_vlm, create=True), \
         patch("clawdbot.visual_scorer._vlm_score", mock_vlm):
        sections = await decompose_reference_site("https://example.com")

    assert len(sections) == 4
    assert sections[0]["type"] == "hero"
    assert sections[3]["type"] == "footer"


@pytest.mark.asyncio
async def test_reference_decomposition_no_screenshot() -> None:
    """decompose_reference_site returns empty on screenshot failure."""
    mock_screenshot = AsyncMock(return_value=None)

    with patch("clawdbot.section_planner._capture_reference_screenshot", mock_screenshot):
        sections = await decompose_reference_site("https://example.com")

    assert sections == []


# ---------------------------------------------------------------------------
# Section plan priorities
# ---------------------------------------------------------------------------


def test_section_plan_priorities() -> None:
    """Hero and CTA have priority 1; services = 2; footer = 3."""
    from clawdbot.section_planner import _SECTION_PRIORITY

    assert _SECTION_PRIORITY["hero"] == 1
    assert _SECTION_PRIORITY["cta"] == 1
    assert _SECTION_PRIORITY["services"] == 2
    assert _SECTION_PRIORITY["features"] == 2
    assert _SECTION_PRIORITY["footer"] == 3
    assert _SECTION_PRIORITY["navbar"] == 3


# ---------------------------------------------------------------------------
# Integration: lead dict -> complete BuildPlan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_to_buildplan_integration(sample_lead: dict) -> None:
    """Full integration: lead dict produces a valid BuildPlan."""
    with patch("clawdbot.section_planner.load_taste_profile", return_value={}), \
         patch("clawdbot.section_planner.select_snippets_for_build", return_value={}):
        plan = await generate_build_plan(sample_lead, site_type="demo")

    # Plan is complete
    assert isinstance(plan, BuildPlan)
    assert plan.total_sections == len(plan.sections)

    # Business context is populated
    assert plan.business_context["name"] == "Bright Smile Dental"
    assert plan.business_context["industry"] == "dentist"

    # Design contract is populated
    assert "palette" in plan.design_contract
    assert "typography" in plan.design_contract
    assert "brand" in plan.design_contract

    # Each section has content
    for sp in plan.sections:
        assert sp.content is not None
        assert isinstance(sp.content, dict)

    # CDN deps are resolved URLs
    for url in plan.cdn_deps:
        assert url.startswith("http")
