"""Tests for the site quality pipeline orchestration."""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict

from tools.site_quality_pipeline import (
    SiteBuildResult,
    build_and_verify_site,
    _brief_to_sections,
    _run_code_review,
    _enrich_brief_with_components,
    _estimate_qa_cost,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_brief():
    return {
        "business_name": "Acme Plumbing",
        "industry": "plumbing",
        "services": ["drain cleaning", "pipe repair"],
        "pages": ["home", "services", "about", "contact"],
        "tone": "friendly and professional",
    }


def _make_agent_result(success=True, cost=0.50, files=None, error=""):
    """Build a mock AgentResult-like object."""
    from tools.claude_code_tool import AgentResult
    return AgentResult(
        success=success,
        output="Site built" if success else "",
        files_created=files or ["index.html", "styles.css"],
        cost_usd=cost,
        turns_used=10,
        error=error,
    )


def _make_qa_result(action="pass", avg_score=7.5, attempt=0, feedback=""):
    """Build a mock QAResult-like object."""
    from tools.visual_qa_gate import QAResult
    return QAResult(
        passed=(action == "pass"),
        average_score=avg_score,
        scores={
            "visual_hierarchy": avg_score,
            "spacing_alignment": avg_score,
            "typography": avg_score,
            "color_harmony": avg_score,
            "component_quality": avg_score,
            "mobile_responsiveness": avg_score,
            "professional_polish": avg_score,
        },
        feedback=feedback,
        action=action,
        attempt=attempt,
    )


def _patch_pipeline(
    build_site_mock,
    qa_loop_mock,
    tmp_path,
    inspiration=None,
    sdk_available=True,
    server_url="http://localhost:9999",
):
    """Return a context manager stack that patches the full pipeline seams."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch(
                "tools.site_quality_pipeline._get_agent_sdk_available",
                return_value=lambda: sdk_available,
            ),
            patch(
                "tools.site_quality_pipeline._get_build_site",
                return_value=build_site_mock,
            ),
            patch(
                "tools.site_quality_pipeline._get_run_qa_loop",
                return_value=qa_loop_mock,
            ),
            patch(
                "tools.site_quality_pipeline._fetch_component_inspiration",
                new_callable=AsyncMock,
                return_value=inspiration or {},
            ),
            patch(
                "tools.site_quality_pipeline._start_local_server",
                new_callable=AsyncMock,
                return_value=(MagicMock(), server_url) if server_url else (None, None),
            ),
            patch("tools.site_quality_pipeline._stop_local_server"),
            patch("tools.site_quality_pipeline._log_activity"),
            patch(
                "tools.site_quality_pipeline.tempfile.mkdtemp",
                return_value=str(tmp_path),
            ),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSiteBuildResult:
    """test_build_result_dataclass"""

    def test_defaults(self):
        r = SiteBuildResult(success=False)
        assert r.success is False
        assert r.site_dir == ""
        assert r.deploy_url == ""
        assert r.qa_score == 0.0
        assert r.qa_scores == {}
        assert r.build_cost_usd == 0.0
        assert r.qa_cost_usd == 0.0
        assert r.total_cost_usd == 0.0
        assert r.attempts == 0
        assert r.files_created == []
        assert r.error == ""
        assert r.escalated is False

    def test_serializable(self):
        r = SiteBuildResult(success=True, qa_score=8.0, files_created=["a.html"])
        d = asdict(r)
        assert d["success"] is True
        assert d["qa_score"] == 8.0
        assert d["files_created"] == ["a.html"]

    def test_cost_fields_independent(self):
        r = SiteBuildResult(
            success=True,
            build_cost_usd=1.50,
            qa_cost_usd=0.09,
            total_cost_usd=1.59,
        )
        assert r.build_cost_usd == 1.50
        assert r.qa_cost_usd == 0.09
        assert r.total_cost_usd == 1.59


@pytest.mark.asyncio
class TestPipelineSuccessFirstAttempt:
    """test_pipeline_success_first_attempt -- mock all tools, QA passes first try."""

    async def test_full_pass(self, sample_brief, tmp_path):
        agent_result = _make_agent_result(success=True, cost=0.80, files=["index.html"])
        qa_result = _make_qa_result(action="pass", avg_score=7.8, attempt=0)

        build_mock = AsyncMock(return_value=agent_result)
        qa_mock = AsyncMock(return_value=qa_result)

        with _patch_pipeline(
            build_mock, qa_mock, tmp_path,
            inspiration={"HeroSection": "<div>Hero</div>"},
        ):
            result = await build_and_verify_site(sample_brief, deploy=False, max_attempts=3)

        assert result.success is True
        assert result.qa_score == 7.8
        assert result.attempts == 1
        assert result.build_cost_usd == 0.80
        assert result.escalated is False
        assert result.error == ""
        build_mock.assert_called_once()


@pytest.mark.asyncio
class TestPipelineRegenerates:
    """test_pipeline_regenerates_on_low_score -- QA returns 6.0, regenerates, then passes."""

    async def test_regenerate_then_pass(self, sample_brief, tmp_path):
        agent_result = _make_agent_result(success=True, cost=0.60)

        build_mock = AsyncMock(return_value=agent_result)

        # Simulate run_qa_loop calling the regeneration callback once, then passing.
        async def fake_qa_loop(site_url, regenerate_callback=None):
            if regenerate_callback:
                new_url = await regenerate_callback("Improve hero spacing")
                assert new_url is not None
            return _make_qa_result(action="pass", avg_score=7.5, attempt=1)

        with _patch_pipeline(build_mock, fake_qa_loop, tmp_path):
            result = await build_and_verify_site(sample_brief, deploy=False, max_attempts=3)

        assert result.success is True
        assert result.qa_score == 7.5
        # Initial build + 1 regeneration = 2 attempts
        assert result.attempts == 2
        # Two builds at 0.60 each
        assert result.build_cost_usd == pytest.approx(1.20, abs=0.01)
        assert result.escalated is False


@pytest.mark.asyncio
class TestPipelineEscalates:
    """test_pipeline_escalates_on_very_low_score -- QA returns 3.0, escalates."""

    async def test_escalate(self, sample_brief, tmp_path):
        agent_result = _make_agent_result(success=True, cost=0.50)
        qa_result = _make_qa_result(
            action="escalate", avg_score=3.0, attempt=0,
            feedback="Site is fundamentally broken",
        )

        build_mock = AsyncMock(return_value=agent_result)
        qa_mock = AsyncMock(return_value=qa_result)

        with _patch_pipeline(build_mock, qa_mock, tmp_path):
            result = await build_and_verify_site(sample_brief)

        assert result.success is False
        assert result.escalated is True
        assert result.qa_score == 3.0
        assert "escalated" in result.error.lower()


@pytest.mark.asyncio
class TestPipelineWithout21stDev:
    """test_pipeline_without_21st_dev -- still builds, just no components."""

    async def test_no_21st_dev(self, sample_brief, tmp_path):
        agent_result = _make_agent_result(success=True, cost=0.70)
        qa_result = _make_qa_result(action="pass", avg_score=7.2)

        build_mock = AsyncMock(return_value=agent_result)
        qa_mock = AsyncMock(return_value=qa_result)

        with _patch_pipeline(build_mock, qa_mock, tmp_path, inspiration={}):
            result = await build_and_verify_site(sample_brief)

        assert result.success is True
        # build_site called with use_21st_dev=False since components dict was empty
        call_kwargs = build_mock.call_args
        assert call_kwargs.kwargs.get("use_21st_dev") is False


@pytest.mark.asyncio
class TestPipelineWithoutVisualQA:
    """test_pipeline_without_visual_qa -- builds, skips QA, returns success."""

    async def test_no_visual_qa(self, sample_brief, tmp_path):
        agent_result = _make_agent_result(success=True, cost=0.55)
        build_mock = AsyncMock(return_value=agent_result)

        # _get_run_qa_loop returns None => visual QA unavailable
        with _patch_pipeline(build_mock, None, tmp_path):
            result = await build_and_verify_site(sample_brief)

        assert result.success is True
        assert result.qa_score == 0.0
        assert result.build_cost_usd == 0.55
        assert result.total_cost_usd == 0.55


@pytest.mark.asyncio
class TestCostTracking:
    """test_cost_tracking_across_steps -- verify costs accumulate correctly."""

    async def test_costs_accumulate(self, sample_brief, tmp_path):
        # Build costs 0.80 each time
        agent_result = _make_agent_result(success=True, cost=0.80)
        build_mock = AsyncMock(return_value=agent_result)

        async def fake_qa_loop(site_url, regenerate_callback=None):
            if regenerate_callback:
                await regenerate_callback("Fix mobile layout")
            # attempt=1 means 2 QA evaluations happened
            return _make_qa_result(action="pass", avg_score=7.3, attempt=1)

        with _patch_pipeline(build_mock, fake_qa_loop, tmp_path):
            result = await build_and_verify_site(sample_brief)

        assert result.success is True
        # 2 builds x $0.80 = $1.60
        assert result.build_cost_usd == pytest.approx(1.60, abs=0.01)
        # 2 QA attempts x 3 viewports x $0.01 = $0.06
        assert result.qa_cost_usd == pytest.approx(0.06, abs=0.01)
        # Total
        assert result.total_cost_usd == pytest.approx(1.66, abs=0.02)
        assert result.attempts == 2


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_brief_to_sections_all_pages(self, sample_brief):
        sections = _brief_to_sections(sample_brief)
        names = [s["name"] for s in sections]
        assert "HeroSection" in names
        assert "ServicesGrid" in names
        assert "AboutSection" in names
        assert "ContactForm" in names

    def test_brief_to_sections_minimal(self):
        sections = _brief_to_sections({"pages": ["home"]})
        assert len(sections) == 1
        assert sections[0]["name"] == "HeroSection"

    def test_enrich_brief_with_components(self, sample_brief):
        components = {"HeroSection": "<div>code</div>", "Missing": None}
        enriched = _enrich_brief_with_components(sample_brief, components)
        assert "component_inspiration" in enriched
        assert "HeroSection" in enriched["component_inspiration"]
        assert "Missing" not in enriched["component_inspiration"]

    def test_enrich_brief_empty_components(self, sample_brief):
        enriched = _enrich_brief_with_components(sample_brief, {})
        assert "component_inspiration" not in enriched

    def test_code_review_empty_dir(self, tmp_path):
        passed, issues = _run_code_review(str(tmp_path))
        assert passed is False
        assert any("No files" in i for i in issues)

    def test_code_review_with_html(self, tmp_path):
        (tmp_path / "index.html").write_text("<html><body>Hi</body></html>")
        passed, issues = _run_code_review(str(tmp_path))
        assert passed is True
        assert issues == []

    def test_code_review_detects_secret(self, tmp_path):
        (tmp_path / "app.js").write_text("const key = 'sk-ant-abc123';")
        passed, issues = _run_code_review(str(tmp_path))
        assert any("sk-ant-" in i for i in issues)

    def test_estimate_qa_cost(self):
        assert _estimate_qa_cost(1) == pytest.approx(0.03)
        assert _estimate_qa_cost(3) == pytest.approx(0.09)

    def test_nonexistent_dir(self):
        passed, issues = _run_code_review("/nonexistent/path/xyz")
        assert passed is False
