"""Tests for clawdbot/section_orchestrator.py — Phase 36 parallel orchestrator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stubs
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
    content: dict[str, Any] = field(default_factory=dict)
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
class _StubBuildPlan:
    sections: list[_StubSectionPlan] = field(default_factory=list)
    design_tokens: _StubDesignTokens = field(default_factory=_StubDesignTokens)
    design_contract: dict[str, Any] = field(default_factory=lambda: {
        "palette": {}, "typography": {}, "spacing": {}, "brand": {},
    })
    direction_name: str = "cosmos-dark-curation"
    site_type: str = "demo"
    page_count: int = 1
    business_context: dict[str, Any] = field(default_factory=dict)
    cdn_deps: list[str] = field(default_factory=list)
    total_sections: int = 0
    estimated_build_time_s: int = 0
    estimated_cost_usd: float = 0.0
    tier: _StubBuildTier = field(default_factory=_StubBuildTier)


_GOOD_HTML = """\
<section class="bg-gray-900 py-20 px-6">
  <div class="max-w-7xl mx-auto text-center">
    <h1 class="text-5xl font-bold text-white mb-4">Hello World</h1>
    <p class="text-xl text-gray-300">Description here.</p>
  </div>
</section>"""


def _make_build_plan(section_types: list[str]) -> _StubBuildPlan:
    """Create a BuildPlan stub with the given section types."""
    plans = [
        _StubSectionPlan(section_type=st, order=i)
        for i, st in enumerate(section_types)
    ]
    bp = _StubBuildPlan(sections=plans, total_sections=len(plans))
    return bp


# ---------------------------------------------------------------------------
# Tests: build_all_sections
# ---------------------------------------------------------------------------


class TestBuildAllSections:
    @pytest.mark.asyncio
    async def test_all_sections_returned(self):
        """Four sections, all succeed."""
        types = ["hero", "services", "contact", "footer"]
        bp = _make_build_plan(types)

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            from clawdbot.section_agent import SectionResult
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=500,
                generation_cost_usd=0.0075,
                total_time_s=2.0,
            )

        with patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate):
            from clawdbot.section_orchestrator import build_all_sections
            result = await build_all_sections(bp, pool=None)

        assert len(result.sections) == 4
        assert result.all_passed is True
        assert result.failed_sections == []
        for st in types:
            assert st in result.sections
            assert result.sections[st].passed is True

    @pytest.mark.asyncio
    async def test_failed_section_not_blocking(self):
        """One section fails, others succeed."""
        types = ["hero", "services", "contact"]
        bp = _make_build_plan(types)

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            from clawdbot.section_agent import SectionResult
            if plan.section_type == "services":
                return SectionResult(
                    section_type=plan.section_type,
                    html="",
                    screenshot=None,
                    score=None,
                    iterations=3,
                    passed=False,
                    error="LLM returned garbage",
                    generation_tokens=300,
                    generation_cost_usd=0.005,
                    total_time_s=5.0,
                )
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=500,
                generation_cost_usd=0.0075,
                total_time_s=2.0,
            )

        with patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate):
            from clawdbot.section_orchestrator import build_all_sections
            result = await build_all_sections(bp, pool=None)

        assert result.all_passed is False
        assert "services" in result.failed_sections
        assert result.sections["hero"].passed is True
        assert result.sections["contact"].passed is True

    @pytest.mark.asyncio
    async def test_exception_in_section_handled(self):
        """If generate_section raises, it's caught by gather."""
        types = ["hero", "services"]
        bp = _make_build_plan(types)

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            if plan.section_type == "services":
                raise RuntimeError("Ollama down")
            from clawdbot.section_agent import SectionResult
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=500,
                generation_cost_usd=0.0075,
                total_time_s=2.0,
            )

        with patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate):
            from clawdbot.section_orchestrator import build_all_sections
            result = await build_all_sections(bp, pool=None)

        assert "services" in result.failed_sections
        assert result.sections["services"].passed is False
        assert "Ollama down" in (result.sections["services"].error or "")
        assert result.sections["hero"].passed is True

    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Verify sections run concurrently via asyncio.gather."""
        types = ["hero", "services", "contact", "footer"]
        bp = _make_build_plan(types)

        running = []

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            running.append(plan.section_type)
            await asyncio.sleep(0.01)  # simulate work
            from clawdbot.section_agent import SectionResult
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=500,
                generation_cost_usd=0.0075,
                total_time_s=0.01,
            )

        with patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate):
            from clawdbot.section_orchestrator import build_all_sections
            result = await build_all_sections(bp, pool=None)

        # All 4 sections should have been started
        assert len(running) == 4
        assert result.all_passed is True


class TestCostTracking:
    @pytest.mark.asyncio
    async def test_cost_estimates(self):
        """Verify cost tracking aggregation."""
        types = ["hero", "services"]
        bp = _make_build_plan(types)

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            from clawdbot.section_agent import SectionResult
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=1000,
                generation_cost_usd=0.015,
                total_time_s=3.0,
            )

        with patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate):
            from clawdbot.section_orchestrator import build_all_sections
            result = await build_all_sections(bp, pool=None)

        assert result.total_cost_usd == pytest.approx(0.030, abs=0.001)
        assert result.total_iterations == 2
        assert "hero" in result.section_costs
        assert result.section_costs["hero"].generation_tokens == 1000

    def test_estimate_section_cost_smart(self):
        from clawdbot.section_orchestrator import estimate_section_cost

        cost = estimate_section_cost("smart", 1000)
        assert cost == pytest.approx(0.015, abs=0.001)

    def test_estimate_section_cost_ollama(self):
        from clawdbot.section_orchestrator import estimate_section_cost

        cost = estimate_section_cost("ollama:qwen2.5-coder:14b", 5000)
        assert cost == 0.0

    def test_estimate_section_cost_fast(self):
        from clawdbot.section_orchestrator import estimate_section_cost

        cost = estimate_section_cost("fast", 1000)
        assert cost == pytest.approx(0.001, abs=0.0001)

    def test_estimate_section_cost_genius(self):
        from clawdbot.section_orchestrator import estimate_section_cost

        cost = estimate_section_cost("genius", 1000)
        assert cost == pytest.approx(0.075, abs=0.001)


class TestProgressEvents:
    @pytest.mark.asyncio
    async def test_progress_emission_calls(self):
        """Verify _emit_progress is called for each section."""
        types = ["hero", "services"]
        bp = _make_build_plan(types)

        async def mock_generate(plan, contract, tokens, pool=None, tier=None):
            from clawdbot.section_agent import SectionResult
            return SectionResult(
                section_type=plan.section_type,
                html=_GOOD_HTML,
                screenshot=None,
                score=None,
                iterations=1,
                passed=True,
                error=None,
                generation_tokens=500,
                generation_cost_usd=0.0075,
                total_time_s=2.0,
            )

        with (
            patch("clawdbot.section_orchestrator.generate_section", side_effect=mock_generate),
            patch("clawdbot.section_orchestrator._emit_progress", new_callable=AsyncMock) as mock_emit,
        ):
            from clawdbot.section_orchestrator import build_all_sections
            await build_all_sections(bp, pool=None)

        # Should emit "generating" + "passed" for each section = 4 calls
        assert mock_emit.call_count == 4
        # Check statuses
        statuses = [call.args[1] for call in mock_emit.call_args_list]
        assert "generating" in statuses
        assert "passed" in statuses
