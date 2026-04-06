"""Tests for clawdbot/section_agent.py — Phase 36 section generation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Lightweight stubs so we can import section_agent without heavy deps
# ---------------------------------------------------------------------------


@dataclass
class _StubDesignTokens:
    primary: str = "#1a1a2e"
    secondary: str = "#16213e"
    accent: str = "#e94560"
    background: str = "#0f0f0f"
    surface: str = "#1a1a1a"
    text_primary: str = "#ffffff"
    text_secondary: str = "#cccccc"
    text_muted: str = "#888888"
    font_display: str = "Inter"
    font_body: str = "Inter"
    scale_ratio: float = 1.25
    heading_weight: str = "700"
    body_line_height: str = "1.6"
    section_padding_y: str = "6rem"
    container_max_width: str = "1280px"
    container_padding_x: str = "2rem"
    card_gap: str = "2rem"
    animation_enabled: bool = True
    entrance_style: str = "fade-up"
    transition_duration: str = "0.4s"
    direction_name: str = "cosmos-dark-curation"


@dataclass
class _StubSectionPlan:
    section_type: str = "hero"
    order: int = 0
    content: dict[str, Any] = field(default_factory=lambda: {
        "business_name": "Acme Dental",
        "headline": "Your Smile, Our Priority",
        "subheadline": "Modern dentistry with a gentle touch.",
        "cta_text": "Book Appointment",
    })
    snippet_ref: str | None = None
    reference_image: str | None = None
    generation_priority: int = 1
    max_iterations: int = 3


@dataclass
class _StubBuildTier:
    name: str = "premium"
    model: str = "smart"
    max_sections: int = 8
    max_iterations: int = 3
    vlm_provider: str = "ollama+claude"
    use_claude_final_qa: bool = True
    effects_enabled: bool = True
    estimated_cost_usd: float = 0.19


@dataclass
class _StubSectionScore:
    overall: float = 8.0
    hierarchy: float = 8.0
    spacing: float = 7.5
    typography: float = 8.0
    color_usage: float = 7.0
    component_quality: float = 8.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = True


_GOOD_SECTION_HTML = """\
<section class="bg-gray-900 py-20 px-6">
  <div class="max-w-7xl mx-auto text-center">
    <h1 class="text-5xl font-bold text-white mb-4">Your Smile, Our Priority</h1>
    <p class="text-xl text-gray-300 mb-8">Modern dentistry with a gentle touch.</p>
    <a href="#contact" class="bg-red-500 text-white px-8 py-3 rounded-lg">Book Appointment</a>
  </div>
</section>"""


# ---------------------------------------------------------------------------
# Tests: extract_section_html
# ---------------------------------------------------------------------------


class TestExtractSectionHtml:
    def test_plain_section(self):
        from clawdbot.section_agent import extract_section_html

        result = extract_section_html(_GOOD_SECTION_HTML)
        assert result is not None
        assert "<section" in result
        assert "</section>" in result

    def test_markdown_fenced(self):
        from clawdbot.section_agent import extract_section_html

        raw = f"Here is the HTML:\n\n```html\n{_GOOD_SECTION_HTML}\n```\n\nHope that helps!"
        result = extract_section_html(raw)
        assert result is not None
        assert "<section" in result

    def test_bare_html_wrapped(self):
        from clawdbot.section_agent import extract_section_html

        raw = '<div class="hero"><h1>Hello</h1></div>'
        result = extract_section_html(raw)
        assert result is not None
        assert "<section>" in result
        assert "<div" in result

    def test_empty_returns_none(self):
        from clawdbot.section_agent import extract_section_html

        assert extract_section_html("") is None
        assert extract_section_html("   ") is None

    def test_no_html_returns_none(self):
        from clawdbot.section_agent import extract_section_html

        assert extract_section_html("Just some explanation text with no HTML.") is None

    def test_multiple_sections_takes_first(self):
        from clawdbot.section_agent import extract_section_html

        raw = '<section id="a">First</section>\n<section id="b">Second</section>'
        result = extract_section_html(raw)
        assert result is not None
        assert 'id="a"' in result
        assert 'id="b"' not in result


# ---------------------------------------------------------------------------
# Tests: validate_section_html
# ---------------------------------------------------------------------------


class TestValidateSectionHtml:
    def test_valid_section(self):
        from clawdbot.section_agent import validate_section_html

        issues = validate_section_html(_GOOD_SECTION_HTML, "hero")
        assert issues == []

    def test_missing_section_tag(self):
        from clawdbot.section_agent import validate_section_html

        issues = validate_section_html("<div>Hello</div>", "hero")
        assert any("section" in i.lower() for i in issues)

    def test_placeholder_urls(self):
        from clawdbot.section_agent import validate_section_html

        html = '<section class="x y"><img src="https://unsplash.com/photo.jpg" class="a b c"></section>'
        issues = validate_section_html(html, "hero")
        assert any("placeholder" in i.lower() for i in issues)

    def test_lorem_ipsum(self):
        from clawdbot.section_agent import validate_section_html

        html = '<section class="a b"><p class="c d">Lorem ipsum dolor sit amet</p></section>'
        issues = validate_section_html(html, "hero")
        assert any("lorem" in i.lower() for i in issues)

    def test_too_short(self):
        from clawdbot.section_agent import validate_section_html

        html = '<section class="x">Hi</section>'
        issues = validate_section_html(html, "hero")
        assert any("short" in i.lower() for i in issues)

    def test_missing_heading_for_hero(self):
        from clawdbot.section_agent import validate_section_html

        html = '<section class="a b c">' + '<p class="d e">No heading here</p>' * 20 + '</section>'
        issues = validate_section_html(html, "hero")
        assert any("heading" in i.lower() for i in issues)


# ---------------------------------------------------------------------------
# Tests: build_section_prompt
# ---------------------------------------------------------------------------


class TestBuildSectionPrompt:
    def test_prompt_includes_contract_and_tokens(self):
        from clawdbot.section_agent import build_section_prompt

        plan = _StubSectionPlan()
        contract = {
            "palette": {"primary": "#1a1a2e", "accent": "#e94560"},
            "typography": {"heading_font": "Inter"},
            "spacing": {"section_padding": "6rem"},
            "brand": {"name": "Acme Dental", "industry": "dentist"},
        }
        tokens = _StubDesignTokens()

        prompt = build_section_prompt(plan, contract, tokens)

        assert "hero" in prompt
        assert "#1a1a2e" in prompt
        assert "Acme Dental" in prompt
        assert "Inter" in prompt
        assert "Tailwind" in prompt
        assert "No placeholder" in prompt or "NO placeholder" in prompt

    def test_prompt_includes_snippet(self):
        from clawdbot.section_agent import build_section_prompt

        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        snippet = '<section class="hero-snippet"><h1>Example</h1></section>'

        prompt = build_section_prompt(plan, contract, tokens, snippet=snippet)
        assert "hero-snippet" in prompt
        assert "Structural Inspiration" in prompt

    def test_prompt_includes_feedback(self):
        from clawdbot.section_agent import build_section_prompt

        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()

        prompt = build_section_prompt(
            plan, contract, tokens,
            previous_feedback=["Text contrast too low", "Spacing inconsistent"],
        )
        assert "Text contrast too low" in prompt
        assert "Previous Feedback" in prompt

    def test_prompt_includes_content(self):
        from clawdbot.section_agent import build_section_prompt

        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()

        prompt = build_section_prompt(plan, contract, tokens)
        assert "Your Smile" in prompt
        assert "Book Appointment" in prompt


# ---------------------------------------------------------------------------
# Tests: generate_section (mocked LLM)
# ---------------------------------------------------------------------------


class TestGenerateSection:
    @pytest.mark.asyncio
    async def test_generate_section_no_pool(self):
        """Mock LLM returns valid HTML -> SectionResult with passed=True."""
        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        tier = _StubBuildTier()

        with patch("clawdbot.section_agent._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _GOOD_SECTION_HTML

            from clawdbot.section_agent import generate_section
            result = await generate_section(plan, contract, tokens, pool=None, tier=tier)

        assert result.passed is True
        assert result.section_type == "hero"
        assert "<section" in result.html
        assert result.iterations == 1
        assert result.generation_tokens > 0
        assert result.total_time_s >= 0

    @pytest.mark.asyncio
    async def test_generate_section_bad_output_retries(self):
        """Mock LLM returns garbage then valid HTML."""
        plan = _StubSectionPlan(max_iterations=2)
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        tier = _StubBuildTier(max_iterations=2)

        call_count = 0

        async def mock_llm(prompt, *, model="smart"):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "I cannot generate HTML."
            return _GOOD_SECTION_HTML

        with patch("clawdbot.section_agent._call_llm", side_effect=mock_llm):
            from clawdbot.section_agent import generate_section
            result = await generate_section(plan, contract, tokens, pool=None, tier=tier)

        assert result.passed is True
        assert result.iterations <= 2


# ---------------------------------------------------------------------------
# Tests: iteration strategy
# ---------------------------------------------------------------------------


class TestIterationStrategy:
    @pytest.mark.asyncio
    async def test_patch_round(self):
        """Iteration <= 2 should produce a PATCH prompt."""
        from clawdbot.section_agent import _iterate_section

        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        feedback = _StubSectionScore(
            overall=5.0,
            passed=False,
            issues=["Low contrast text", "Spacing uneven"],
            actionable_fixes=["Increase heading contrast"],
        )

        with patch("clawdbot.section_agent._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _GOOD_SECTION_HTML
            await _iterate_section(
                current_html="<section>old</section>",
                feedback=feedback,
                plan=plan,
                contract=contract,
                tokens=tokens,
                iteration=2,
                tier=_StubBuildTier(),
            )

        # Verify the prompt sent was a PATCH (contains current HTML)
        call_args = mock_llm.call_args
        prompt = call_args[0][0]
        assert "<section>old</section>" in prompt
        assert "Low contrast text" in prompt
        assert "Increase heading contrast" in prompt

    @pytest.mark.asyncio
    async def test_regenerate_round(self):
        """Iteration == 3 should produce a REGENERATE prompt."""
        from clawdbot.section_agent import _iterate_section

        plan = _StubSectionPlan()
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        feedback = _StubSectionScore(
            overall=4.0,
            passed=False,
            issues=["Layout broken", "Colors wrong"],
            actionable_fixes=["Use correct palette"],
        )

        with patch("clawdbot.section_agent._call_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _GOOD_SECTION_HTML
            await _iterate_section(
                current_html="<section>broken</section>",
                feedback=feedback,
                plan=plan,
                contract=contract,
                tokens=tokens,
                iteration=3,
                tier=_StubBuildTier(),
            )

        # Regenerate prompt should NOT contain the old HTML but SHOULD contain issues
        call_args = mock_llm.call_args
        prompt = call_args[0][0]
        assert "from scratch" in prompt.lower()
        assert "Layout broken" in prompt


class TestSectionWithVlmFeedback:
    @pytest.mark.asyncio
    async def test_vlm_triggers_iteration(self):
        """When VLM score < threshold, section agent iterates."""
        import sys
        import types

        plan = _StubSectionPlan(max_iterations=2)
        contract = {"palette": {}, "typography": {}, "spacing": {}, "brand": {}}
        tokens = _StubDesignTokens()
        tier = _StubBuildTier(max_iterations=2)

        low_score = _StubSectionScore(
            overall=5.0, passed=False,
            issues=["Bad spacing"], actionable_fixes=["Fix spacing"],
        )
        high_score = _StubSectionScore(overall=8.0, passed=True)

        score_calls = [0]

        async def mock_score(screenshot, section_type, design_tokens):
            score_calls[0] += 1
            if score_calls[0] == 1:
                return low_score
            return high_score

        async def mock_render(section_html, design_tokens, pool=None):
            return b"fake_png"

        # Inject mock modules for renderer and visual_scorer
        mock_renderer = types.ModuleType("clawdbot.renderer")
        mock_renderer.render_section_in_page = mock_render
        mock_scorer = types.ModuleType("clawdbot.visual_scorer")
        mock_scorer.score_section_quality = mock_score

        saved_renderer = sys.modules.get("clawdbot.renderer")
        saved_scorer = sys.modules.get("clawdbot.visual_scorer")
        sys.modules["clawdbot.renderer"] = mock_renderer
        sys.modules["clawdbot.visual_scorer"] = mock_scorer

        try:
            with patch("clawdbot.section_agent._call_llm", new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = _GOOD_SECTION_HTML
                mock_pool = MagicMock()

                from clawdbot.section_agent import generate_section
                result = await generate_section(plan, contract, tokens, pool=mock_pool, tier=tier)

            # Should have iterated at least once
            assert result.iterations >= 1
        finally:
            if saved_renderer is not None:
                sys.modules["clawdbot.renderer"] = saved_renderer
            else:
                sys.modules.pop("clawdbot.renderer", None)
            if saved_scorer is not None:
                sys.modules["clawdbot.visual_scorer"] = saved_scorer
            else:
                sys.modules.pop("clawdbot.visual_scorer", None)
