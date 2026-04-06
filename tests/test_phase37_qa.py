"""Tests for Phase 37: Full-page visual QA pipeline."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock types
# ---------------------------------------------------------------------------


@dataclass
class MockSectionScore:
    overall: float = 8.0
    hierarchy: float = 8.0
    spacing: float = 8.0
    typography: float = 8.0
    color_usage: float = 8.0
    component_quality: float = 8.0
    issues: list[str] = field(default_factory=list)
    actionable_fixes: list[str] = field(default_factory=list)
    passed: bool = True


@dataclass
class MockFullPageScore:
    total: float = 8.5
    color_consistency: float = 0.85
    typography_hierarchy: float = 0.8
    spacing_consistency: float = 0.9
    responsive_mobile: float = 0.8
    responsive_tablet: float = 0.85
    navigation: float = 0.9
    above_fold_impact: float = 0.85
    content_hierarchy: float = 0.8
    animation_presence: float = 0.7
    aesthetic_cohesion: float = 0.85
    mandatory_pass: bool = True
    issues: list[str] = field(default_factory=list)


@dataclass
class MockDesignTokens:
    primary: str = "#1a1a1a"
    accent: str = "#ff6b6b"
    background: str = "#ffffff"
    font_display: str = "Inter"
    font_body: str = "Inter"
    direction_name: str = "cosmos-dark-curation"


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
    name: str = "premium"
    model: str = "smart"
    max_sections: int = 8
    max_iterations: int = 3
    vlm_provider: str = "ollama+claude"
    use_claude_final_qa: bool = True
    effects_enabled: bool = True
    estimated_cost_usd: float = 0.19


@dataclass
class MockBuildPlan:
    sections: list[Any] = field(default_factory=list)
    design_tokens: Any = field(default_factory=MockDesignTokens)
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


SAMPLE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Test Site</title></head>
<body>
  <nav><a href="#">Home</a></nav>
  <main>
    <section class="hero"><h1>Welcome to Test Business</h1></section>
    <section class="services"><h2>Services</h2></section>
  </main>
  <footer><p>Test Business 2026</p></footer>
</body>
</html>"""


def _make_plan(tier_name: str = "demo") -> MockBuildPlan:
    tier = MockBuildTier(name=tier_name)
    return MockBuildPlan(
        sections=[
            MockSectionPlan(section_type="hero", order=0),
            MockSectionPlan(section_type="services", order=1),
        ],
        design_tokens=MockDesignTokens(),
        business_context={"name": "Test Business", "industry": "General"},
        tier=tier,
    )


# ---------------------------------------------------------------------------
# Tests — Full Page QA
# ---------------------------------------------------------------------------


class TestFullPageQA:
    def test_full_page_qa_no_pool(self) -> None:
        """QA without pool should still produce a result."""
        from clawdbot.fullpage_qa import run_full_page_qa

        plan = _make_plan()
        result = asyncio.get_event_loop().run_until_complete(
            run_full_page_qa(SAMPLE_HTML, plan, pool=None)
        )

        assert result is not None
        assert isinstance(result.overall_score, float)
        assert isinstance(result.issues, list)
        assert isinstance(result.passed, bool)

    def test_qa_passes_good_scores(self) -> None:
        """Good markup scores should result in passed=True."""
        from clawdbot.fullpage_qa import run_full_page_qa

        plan = _make_plan()

        with patch("clawdbot.fullpage_qa.analyze_site_markup") as mock_markup:
            mock_markup.return_value = {"score": 0.85, "issues": [], "passed": True}

            result = asyncio.get_event_loop().run_until_complete(
                run_full_page_qa(SAMPLE_HTML, plan, pool=None)
            )

        # Score of 8.5 (0.85 * 10) should pass threshold of 7.0
        assert result.overall_score >= 7.0
        assert result.passed is True

    def test_qa_fails_placeholder_content(self) -> None:
        """HTML with placeholder content should fail mandatory checks."""
        from clawdbot.fullpage_qa import run_full_page_qa

        placeholder_html = SAMPLE_HTML.replace(
            "Welcome to Test Business", "Lorem ipsum dolor sit amet"
        )
        plan = _make_plan()

        with patch("clawdbot.fullpage_qa.analyze_site_markup") as mock_markup:
            mock_markup.return_value = {"score": 0.9, "issues": [], "passed": True}

            result = asyncio.get_event_loop().run_until_complete(
                run_full_page_qa(placeholder_html, plan, pool=None)
            )

        assert result.passed is False
        assert any("placeholder" in issue.lower() or "lorem" in issue.lower()
                    for issue in result.issues)

    def test_qa_fails_secrets_in_html(self) -> None:
        """HTML with API secrets should fail mandatory checks."""
        from clawdbot.fullpage_qa import run_full_page_qa

        secret_html = SAMPLE_HTML.replace(
            "</body>", '<div data-key="sk_live_abc123xyz456">secret</div></body>'
        )
        plan = _make_plan()

        result = asyncio.get_event_loop().run_until_complete(
            run_full_page_qa(secret_html, plan, pool=None)
        )

        assert result.passed is False
        assert any("secret" in issue.lower() for issue in result.issues)

    def test_qa_fails_empty_html(self) -> None:
        """Empty HTML should fail mandatory checks."""
        from clawdbot.fullpage_qa import run_full_page_qa

        plan = _make_plan()

        result = asyncio.get_event_loop().run_until_complete(
            run_full_page_qa("", plan, pool=None)
        )

        assert result.passed is False
        assert any("empty" in issue.lower() for issue in result.issues)

    def test_qa_with_mock_pool(self) -> None:
        """QA with pool should capture screenshots and score viewports."""
        from clawdbot.fullpage_qa import run_full_page_qa

        plan = _make_plan()
        mock_pool = MagicMock()
        mock_pool.render_multi_viewport = AsyncMock(return_value={
            "desktop": b"fake_png_desktop",
            "tablet": b"fake_png_tablet",
            "mobile": b"fake_png_mobile",
        })

        mock_section_score = MockSectionScore(overall=8.0, passed=True)

        with patch("clawdbot.fullpage_qa.analyze_site_markup") as mock_markup, \
             patch("clawdbot.visual_scorer.score_section_quality", new_callable=AsyncMock) as mock_vlm:
            mock_markup.return_value = {"score": 0.8, "issues": [], "passed": True}
            mock_vlm.return_value = mock_section_score

            result = asyncio.get_event_loop().run_until_complete(
                run_full_page_qa(SAMPLE_HTML, plan, pool=mock_pool)
            )

        assert result.screenshots is not None
        assert len(result.screenshots) == 3
        assert "desktop" in result.screenshots


# ---------------------------------------------------------------------------
# Tests — Deploy Gate
# ---------------------------------------------------------------------------


class TestDeployGate:
    def test_deploy_gate_hard_blocks_low_score(self) -> None:
        """Hard gate should block deployment for low scores."""
        from clawdbot.fullpage_qa import FullPageQAResult, deploy_gate

        qa = FullPageQAResult(
            passed=False,
            overall_score=5.0,
            issues=["Low quality"],
        )

        with patch.dict(os.environ, {"VISUAL_QA_BLOCKING": "true"}):
            import clawdbot.fullpage_qa as mod
            mod.VISUAL_QA_BLOCKING = True

            try:
                should_deploy, reason = asyncio.get_event_loop().run_until_complete(
                    deploy_gate(qa)
                )
                assert should_deploy is False
                assert "hard gate" in reason.lower() or "threshold" in reason.lower()
            finally:
                mod.VISUAL_QA_BLOCKING = False

    def test_deploy_gate_soft_allows_low_score(self) -> None:
        """Soft gate should allow deployment for low scores with warning."""
        from clawdbot.fullpage_qa import FullPageQAResult, deploy_gate

        qa = FullPageQAResult(
            passed=False,
            overall_score=5.0,
            issues=["Low quality"],
        )

        # Default is soft gate (VISUAL_QA_BLOCKING=false)
        should_deploy, reason = asyncio.get_event_loop().run_until_complete(
            deploy_gate(qa)
        )

        assert should_deploy is True
        assert "soft gate" in reason.lower()
        assert "warning" in reason.lower()

    def test_deploy_gate_hard_passes_good_score(self) -> None:
        """Hard gate should allow deployment for good scores."""
        import clawdbot.fullpage_qa as mod
        from clawdbot.fullpage_qa import FullPageQAResult, deploy_gate

        qa = FullPageQAResult(
            passed=True,
            overall_score=8.5,
            issues=[],
        )

        mod.VISUAL_QA_BLOCKING = True
        try:
            should_deploy, reason = asyncio.get_event_loop().run_until_complete(
                deploy_gate(qa)
            )
            assert should_deploy is True
            assert "passed" in reason.lower()
        finally:
            mod.VISUAL_QA_BLOCKING = False

    def test_deploy_gate_mandatory_blocks_always(self) -> None:
        """Mandatory blocks should prevent deployment in both modes."""
        from clawdbot.fullpage_qa import FullPageQAResult, deploy_gate

        qa = FullPageQAResult(
            passed=False,
            overall_score=9.0,
            issues=["MANDATORY FAIL: placeholder content detected: 'lorem ipsum'"],
        )

        # Soft gate mode
        should_deploy, reason = asyncio.get_event_loop().run_until_complete(
            deploy_gate(qa)
        )

        assert should_deploy is False
        assert "mandatory" in reason.lower()


# ---------------------------------------------------------------------------
# Tests — Iteration
# ---------------------------------------------------------------------------


class TestIterateFullPage:
    def test_iterate_improves_score(self) -> None:
        """Iteration should attempt to improve failing sections."""
        from clawdbot.fullpage_qa import FullPageQAResult, iterate_full_page

        sections = {
            "hero": MockSectionResult(
                section_type="hero",
                html='<section class="hero"><h1>Old Hero</h1></section>',
            ),
            "services": MockSectionResult(
                section_type="services",
                html='<section class="services"><h2>Services</h2></section>',
            ),
        }

        plan = _make_plan()
        plan.sections = [
            MockSectionPlan(section_type="hero", order=0),
            MockSectionPlan(section_type="services", order=1),
        ]

        initial_qa = FullPageQAResult(
            passed=False,
            overall_score=5.0,
            issues=["hero section has poor above_fold_impact"],
        )

        improved_result = MockSectionResult(
            section_type="hero",
            html='<section class="hero"><h1>Improved Hero</h1></section>',
            passed=True,
        )

        improved_qa = FullPageQAResult(
            passed=True,
            overall_score=8.0,
            issues=[],
        )

        with patch("clawdbot.fullpage_qa.run_full_page_qa", new_callable=AsyncMock) as mock_qa, \
             patch("clawdbot.section_agent.generate_section", new_callable=AsyncMock) as mock_gen, \
             patch("clawdbot.page_assembler.assemble_page", new_callable=AsyncMock) as mock_assemble:
            mock_gen.return_value = improved_result
            mock_assemble.return_value = "<html>improved</html>"
            mock_qa.return_value = improved_qa

            final_html, final_qa = asyncio.get_event_loop().run_until_complete(
                iterate_full_page(
                    SAMPLE_HTML, initial_qa, plan, sections, pool=None, max_iterations=1,
                )
            )

        assert final_qa.passed is True
        assert final_qa.overall_score > initial_qa.overall_score

    def test_iterate_skips_when_passed(self) -> None:
        """If QA already passes, no iteration should happen."""
        from clawdbot.fullpage_qa import FullPageQAResult, iterate_full_page

        qa = FullPageQAResult(passed=True, overall_score=8.5, issues=[])

        final_html, final_qa = asyncio.get_event_loop().run_until_complete(
            iterate_full_page(SAMPLE_HTML, qa, _make_plan(), {}, pool=None)
        )

        assert final_html == SAMPLE_HTML
        assert final_qa.passed is True


# ---------------------------------------------------------------------------
# Tests — Issue-to-Section Mapping
# ---------------------------------------------------------------------------


class TestIssueSectionMapping:
    def test_identify_navbar_issues(self) -> None:
        from clawdbot.fullpage_qa import _identify_failing_sections

        sections = {"navbar": None, "hero": None, "services": None}
        issues = ["navigation links are broken", "nav menu not responsive"]

        failing = _identify_failing_sections(issues, sections)
        assert "navbar" in failing

    def test_identify_hero_issues(self) -> None:
        from clawdbot.fullpage_qa import _identify_failing_sections

        sections = {"navbar": None, "hero": None, "services": None}
        issues = ["hero section above_fold is weak"]

        failing = _identify_failing_sections(issues, sections)
        assert "hero" in failing

    def test_no_issues_returns_empty(self) -> None:
        from clawdbot.fullpage_qa import _identify_failing_sections

        sections = {"hero": None}
        failing = _identify_failing_sections([], sections)
        assert failing == []
