"""Tests for Phase 37: Page assembly + multi-page generation."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch  # noqa: I001

# ---------------------------------------------------------------------------
# Mock dependencies to avoid importing the full stack
# ---------------------------------------------------------------------------


@dataclass
class MockSectionResult:
    section_type: str = ""
    html: str = ""
    screenshot: bytes | None = None
    score: Any = None
    iterations: int = 1
    passed: bool = True
    error: str | None = None
    generation_tokens: int = 0
    generation_cost_usd: float = 0.0
    total_time_s: float = 0.0


@dataclass
class MockSectionPlan:
    section_type: str = ""
    order: int = 0
    content: dict[str, Any] = field(default_factory=dict)
    snippet_ref: str | None = None
    reference_image: str | None = None
    generation_priority: int = 3
    max_iterations: int = 1


@dataclass
class MockBuildTier:
    name: str = "demo"
    model: str = "fast"
    max_sections: int = 4
    max_iterations: int = 1
    vlm_provider: str = "ollama"
    use_claude_final_qa: bool = False
    effects_enabled: bool = False
    estimated_cost_usd: float = 0.0


@dataclass
class MockBuildPlan:
    sections: list[Any] = field(default_factory=list)
    design_tokens: Any = None
    design_contract: dict[str, Any] = field(default_factory=dict)
    direction_name: str = "cosmos-dark-curation"
    site_type: str = "demo"
    page_count: int = 1
    business_context: dict[str, Any] = field(default_factory=dict)
    cdn_deps: list[str] = field(default_factory=list)
    total_sections: int = 0
    estimated_build_time_s: int = 0
    estimated_cost_usd: float = 0.0
    tier: Any = field(default_factory=MockBuildTier)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_sections() -> dict[str, MockSectionResult]:
    return {
        "navbar": MockSectionResult(
            section_type="navbar",
            html='<nav class="fixed top-0 w-full"><a href="#">Home</a><a href="#services">Services</a></nav>',
        ),
        "hero": MockSectionResult(
            section_type="hero",
            html='<section class="hero py-20"><h1>Bright Smile Dental</h1><p>Your trusted dentist</p></section>',
        ),
        "services": MockSectionResult(
            section_type="services",
            html='<section class="services py-16"><h2>Our Services</h2><div>Cleaning, Whitening</div></section>',
        ),
        "testimonials": MockSectionResult(
            section_type="testimonials",
            html='<section class="testimonials py-16"><h2>What Patients Say</h2><p>"Great service!"</p></section>',
        ),
        "footer": MockSectionResult(
            section_type="footer",
            html='<footer class="py-8"><p>Bright Smile Dental &copy; 2026</p></footer>',
        ),
    }


def _make_build_plan() -> MockBuildPlan:
    section_types = ["navbar", "hero", "services", "testimonials", "footer"]
    plan_sections = [
        MockSectionPlan(section_type=st, order=i)
        for i, st in enumerate(section_types)
    ]
    return MockBuildPlan(
        sections=plan_sections,
        design_contract={
            "head_block": '<link rel="stylesheet" href="https://cdn.example.com/tailwind.css">',
            "motion": {"scroll_trigger": True},
        },
        direction_name="cosmos-dark-curation",
        site_type="demo",
        business_context={
            "name": "Bright Smile Dental",
            "industry": "Dentist",
            "tagline": "Your trusted family dentist in Austin",
            "description": "Professional dental care for the whole family",
            "phone": "512-555-0123",
            "email": "info@brightsmile.com",
            "address": "123 Main St, Austin, TX 78701",
            "services": [
                {"name": "Cleaning", "description": "Professional teeth cleaning"},
                {"name": "Whitening", "description": "Teeth whitening services"},
            ],
        },
        total_sections=5,
    )


# ---------------------------------------------------------------------------
# Tests — Page Assembly
# ---------------------------------------------------------------------------


class TestAssemblePage:
    def test_assemble_page_valid_html(self) -> None:
        """Assembled page should be valid HTML with doctype, head, body."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        assert "<!DOCTYPE html>" in html
        assert "<html lang=" in html
        assert "<head>" in html
        assert "<body" in html
        assert "</html>" in html
        assert "<main>" in html
        assert "</main>" in html

    def test_seo_meta_tags(self) -> None:
        """Assembled page should contain SEO meta tags."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        # Title tag
        assert "<title>" in html
        assert "Bright Smile Dental" in html

        # Meta description
        assert 'name="description"' in html

        # Robots
        assert 'name="robots" content="index, follow"' in html

        # Canonical
        assert 'rel="canonical"' in html

        # Open Graph
        assert 'property="og:type"' in html
        assert 'property="og:title"' in html
        assert 'property="og:description"' in html

        # Twitter Card
        assert 'name="twitter:card"' in html
        assert 'name="twitter:title"' in html

    def test_schema_org_jsonld(self) -> None:
        """Assembled page should contain Schema.org LocalBusiness JSON-LD."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        assert 'type="application/ld+json"' in html

        # Extract and parse JSON-LD
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            html, re.DOTALL,
        )
        assert match is not None
        schema = json.loads(match.group(1))
        assert schema["@type"] == "LocalBusiness"
        assert schema["name"] == "Bright Smile Dental"
        assert "telephone" in schema

    def test_section_ordering(self) -> None:
        """Sections should appear in build_plan order."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        # Navbar before main
        nav_pos = html.find("<nav")
        main_pos = html.find("<main>")
        assert nav_pos < main_pos, "Navbar should be before <main>"

        # Hero before services in main
        hero_pos = html.find('class="hero')
        services_pos = html.find('class="services')
        assert hero_pos < services_pos, "Hero should be before services"

        # Services before testimonials
        testimonials_pos = html.find('class="testimonials')
        assert services_pos < testimonials_pos, "Services should be before testimonials"

        # Footer after main
        footer_pos = html.find("<footer")
        main_end = html.find("</main>")
        assert footer_pos > main_end, "Footer should be after </main>"

    def test_gsap_init(self) -> None:
        """Page should include GSAP ScrollTrigger initialization script."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        assert "ScrollTrigger.refresh()" in html
        assert "data-animate" in html

    def test_reduced_motion(self) -> None:
        """Page should include prefers-reduced-motion media query."""
        from clawdbot.page_assembler import assemble_page

        sections = _make_sections()
        plan = _make_build_plan()

        html = asyncio.get_event_loop().run_until_complete(
            assemble_page(sections, plan)
        )

        assert "prefers-reduced-motion" in html

    def test_body_classes_direction(self) -> None:
        """Body should have direction-specific CSS classes."""
        from clawdbot.page_assembler import _build_body_classes

        classes = _build_body_classes("cosmos-dark-curation")
        assert "antialiased" in classes
        assert "bg-neutral-950" in classes

        classes = _build_body_classes("minimal-geometric")
        assert "bg-white" in classes


# ---------------------------------------------------------------------------
# Tests — SEO helpers
# ---------------------------------------------------------------------------


class TestSEOHelpers:
    def test_title_under_60_chars(self) -> None:
        from clawdbot.page_assembler import _build_seo_meta

        meta = _build_seo_meta(
            {"name": "A Very Long Business Name That Could Exceed Limits",
             "industry": "Professional Services",
             "address": "123 Main, Los Angeles, CA"},
            "demo",
        )
        # Extract title
        match = re.search(r"<title>(.*?)</title>", meta)
        assert match is not None
        assert len(match.group(1)) <= 60

    def test_meta_description_under_155_chars(self) -> None:
        from clawdbot.page_assembler import _build_seo_meta

        meta = _build_seo_meta(
            {"name": "Biz", "tagline": "x" * 200}, "demo",
        )
        match = re.search(r'name="description" content="(.*?)"', meta)
        assert match is not None
        assert len(match.group(1)) <= 155

    def test_schema_org_services(self) -> None:
        from clawdbot.page_assembler import _build_schema_org

        schema_html = _build_schema_org({
            "name": "Test Biz",
            "services": [{"name": "Svc1"}, {"name": "Svc2"}],
        })
        schema = json.loads(
            re.search(r"<script[^>]*>(.*?)</script>", schema_html, re.DOTALL).group(1)
        )
        assert schema["@type"] == "LocalBusiness"
        catalog = schema.get("hasOfferCatalog", {})
        items = catalog.get("itemListElement", [])
        assert len(items) == 2


# ---------------------------------------------------------------------------
# Tests — Multi-page Assembly
# ---------------------------------------------------------------------------


class TestMultipageAssembly:
    def test_multipage_returns_five_pages(self) -> None:
        """Multi-page site should return 5 HTML files."""
        from clawdbot.page_assembler import assemble_multipage_site

        sections = _make_sections()
        plan = _make_build_plan()

        with patch("clawdbot.page_assembler._generate_inner_page_content", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = '<section class="inner"><h1>Inner Page</h1></section>'

            pages = asyncio.get_event_loop().run_until_complete(
                assemble_multipage_site(sections, plan)
            )

        assert len(pages) == 5
        assert "index.html" in pages
        assert "about.html" in pages
        assert "services.html" in pages
        assert "gallery.html" in pages
        assert "contact.html" in pages

    def test_shared_nav_footer(self) -> None:
        """All pages should share the same nav and footer."""
        from clawdbot.page_assembler import assemble_multipage_site

        sections = _make_sections()
        plan = _make_build_plan()

        with patch("clawdbot.page_assembler._generate_inner_page_content", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = '<section><h1>Test</h1></section>'

            pages = asyncio.get_event_loop().run_until_complete(
                assemble_multipage_site(sections, plan)
            )

        # All pages should have nav and footer
        for filename, page_html in pages.items():
            assert "<nav" in page_html, f"{filename} missing navbar"
            assert "<footer" in page_html, f"{filename} missing footer"

    def test_nav_links_correct(self) -> None:
        """Nav links should point to correct filenames."""
        from clawdbot.page_assembler import _update_nav_links

        navbar = '<nav><a href="#about">About</a><a href="#services">Services</a><a href="#contact">Contact</a></nav>'
        updated = _update_nav_links(navbar)

        assert 'href="about.html"' in updated
        assert 'href="services.html"' in updated
        assert 'href="contact.html"' in updated

    def test_all_pages_valid_html(self) -> None:
        """Each page should have doctype and standard HTML structure."""
        from clawdbot.page_assembler import assemble_multipage_site

        sections = _make_sections()
        plan = _make_build_plan()

        with patch("clawdbot.page_assembler._generate_inner_page_content", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = '<section><h1>Test</h1></section>'

            pages = asyncio.get_event_loop().run_until_complete(
                assemble_multipage_site(sections, plan)
            )

        for filename, page_html in pages.items():
            assert "<!DOCTYPE html>" in page_html, f"{filename} missing doctype"
            assert "<head>" in page_html, f"{filename} missing head"
            assert "<body" in page_html, f"{filename} missing body"
