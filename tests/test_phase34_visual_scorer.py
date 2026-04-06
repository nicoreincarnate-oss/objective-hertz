"""Tests for clawdbot.visual_scorer — VLM scoring pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clawdbot.visual_scorer import (
    ComparisonScore,
    ContractValidation,
    FullPageScore,
    SectionScore,
    _parse_vlm_json,
    _vlm_score,
    compare_to_reference,
    score_full_page,
    score_section_quality,
    validate_design_contract,
)

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-png-data"


# ---------------------------------------------------------------------------
# _parse_vlm_json
# ---------------------------------------------------------------------------


class TestParseVlmJson:
    def test_plain_json(self):
        assert _parse_vlm_json('{"score": 8}') == {"score": 8}

    def test_markdown_fenced_json(self):
        raw = "```json\n{\"score\": 7}\n```"
        assert _parse_vlm_json(raw) == {"score": 7}

    def test_markdown_fenced_no_lang(self):
        raw = "```\n{\"score\": 6}\n```"
        assert _parse_vlm_json(raw) == {"score": 6}

    def test_json_embedded_in_text(self):
        raw = "Here is my analysis:\n{\"score\": 9}\nDone."
        assert _parse_vlm_json(raw) == {"score": 9}

    def test_empty_returns_empty_dict(self):
        assert _parse_vlm_json("") == {}

    def test_no_json_returns_empty_dict(self):
        assert _parse_vlm_json("This has no JSON at all") == {}

    def test_nested_json(self):
        raw = '{"issues": ["a", "b"], "score": 8.5}'
        result = _parse_vlm_json(raw)
        assert result["score"] == 8.5
        assert result["issues"] == ["a", "b"]


# ---------------------------------------------------------------------------
# _vlm_score
# ---------------------------------------------------------------------------


class TestVlmScore:
    @pytest.mark.asyncio
    async def test_ollama_primary(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": '{"overall": 8.0}'}
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("clawdbot.visual_scorer.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await _vlm_score([FAKE_PNG], "test prompt", provider="ollama")

        assert result["overall"] == 8.0
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_ollama_to_claude(self):
        """When Ollama fails, should fall back to Claude."""
        mock_llm = AsyncMock()
        mock_llm.generate_with_images = AsyncMock(return_value='{"overall": 7.0}')

        with (
            patch("clawdbot.visual_scorer._vlm_ollama", side_effect=ConnectionError("offline")),
            patch("clawdbot.visual_scorer._vlm_claude", return_value={"overall": 7.0, "model": "claude-vision"}),
        ):
            result = await _vlm_score([FAKE_PNG], "test prompt", provider="ollama")

        assert result["overall"] == 7.0
        assert result["model"] == "claude-vision"

    @pytest.mark.asyncio
    async def test_both_fail_returns_fallback(self):
        with (
            patch("clawdbot.visual_scorer._vlm_ollama", side_effect=ConnectionError("offline")),
            patch("clawdbot.visual_scorer._vlm_claude", side_effect=RuntimeError("api error")),
        ):
            result = await _vlm_score([FAKE_PNG], "test prompt", provider="ollama")

        assert result["model"] == "none"
        assert result["raw"] == ""


# ---------------------------------------------------------------------------
# score_section_quality
# ---------------------------------------------------------------------------


class TestScoreSectionQuality:
    @pytest.mark.asyncio
    async def test_returns_section_score(self):
        vlm_result = {
            "overall": 8.5,
            "hierarchy": 9.0,
            "spacing": 8.0,
            "typography": 7.5,
            "color_usage": 8.0,
            "component_quality": 8.5,
            "issues": ["Minor spacing issue"],
            "actionable_fixes": ["Increase padding-top by 1rem"],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens(direction_name="minimal-geometric")

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await score_section_quality(FAKE_PNG, "hero", tokens)

        assert isinstance(result, SectionScore)
        assert result.overall == 8.5
        assert result.passed is True
        assert len(result.issues) == 1
        assert len(result.actionable_fixes) == 1

    @pytest.mark.asyncio
    async def test_failing_score(self):
        vlm_result = {
            "overall": 4.0,
            "hierarchy": 3.0,
            "spacing": 4.0,
            "typography": 5.0,
            "color_usage": 3.0,
            "component_quality": 4.0,
            "issues": ["Bad contrast", "Overlapping elements"],
            "actionable_fixes": [],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await score_section_quality(FAKE_PNG, "hero", tokens)

        assert result.passed is False
        assert result.overall == 4.0


# ---------------------------------------------------------------------------
# compare_to_reference
# ---------------------------------------------------------------------------


class TestCompareToReference:
    @pytest.mark.asyncio
    async def test_passing_comparison(self):
        vlm_result = {
            "match_score": 0.85,
            "color_match": 0.9,
            "typography_match": 0.8,
            "layout_match": 0.85,
            "issues": [],
            "actionable_fixes": [],
        }

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await compare_to_reference(FAKE_PNG, FAKE_PNG, "hero")

        assert isinstance(result, ComparisonScore)
        assert result.passed is True
        assert result.match_score == 0.85

    @pytest.mark.asyncio
    async def test_failing_comparison(self):
        vlm_result = {
            "match_score": 0.3,
            "color_match": 0.2,
            "typography_match": 0.4,
            "layout_match": 0.3,
            "issues": ["Colors don't match"],
            "actionable_fixes": ["Use darker primary"],
        }

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await compare_to_reference(FAKE_PNG, FAKE_PNG, "hero")

        assert result.passed is False
        assert result.match_score == 0.3


# ---------------------------------------------------------------------------
# score_full_page
# ---------------------------------------------------------------------------


class TestScoreFullPage:
    @pytest.mark.asyncio
    async def test_full_page_rubric(self):
        vlm_result = {
            "color_consistency": 0.9,
            "typography_hierarchy": 0.8,
            "spacing_consistency": 0.85,
            "responsive_mobile": 0.7,
            "responsive_tablet": 0.75,
            "navigation": 0.8,
            "above_fold_impact": 0.9,
            "content_hierarchy": 0.85,
            "animation_presence": 0.5,
            "aesthetic_cohesion": 0.8,
            "issues": ["Animations could be smoother"],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens(direction_name="dark-cinematic")
        screenshots = {"desktop": FAKE_PNG, "tablet": FAKE_PNG, "mobile": FAKE_PNG}

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await score_full_page(screenshots, tokens, {"name": "Test Co"})

        assert isinstance(result, FullPageScore)
        assert result.total == pytest.approx(7.85, abs=0.01)
        assert result.mandatory_pass is True
        assert result.responsive_mobile == 0.7
        assert len(result.issues) == 1

    @pytest.mark.asyncio
    async def test_mandatory_fail(self):
        vlm_result = {
            "color_consistency": 0.9,
            "typography_hierarchy": 0.8,
            "spacing_consistency": 0.85,
            "responsive_mobile": 0.3,  # fails mandatory
            "responsive_tablet": 0.75,
            "navigation": 0.8,
            "above_fold_impact": 0.9,
            "content_hierarchy": 0.85,
            "animation_presence": 0.5,
            "aesthetic_cohesion": 0.8,
            "issues": [],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()
        screenshots = {"desktop": FAKE_PNG, "mobile": FAKE_PNG}

        with patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result):
            result = await score_full_page(screenshots, tokens, {})

        assert result.mandatory_pass is False


# ---------------------------------------------------------------------------
# validate_design_contract
# ---------------------------------------------------------------------------


class TestValidateDesignContract:
    @pytest.mark.asyncio
    async def test_passing_contract(self):
        vlm_result = {
            "contrast_ok": True,
            "readability_ok": True,
            "aesthetic_ok": True,
            "issues": [],
            "suggested_adjustments": [],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()
        mock_pool = AsyncMock()

        mock_render = AsyncMock(return_value=FAKE_PNG)
        with (
            patch("clawdbot.renderer.render_section_in_page", mock_render),
            patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result),
        ):
            result = await validate_design_contract(tokens, mock_pool)

        assert isinstance(result, ContractValidation)
        assert result.passed is True
        assert result.contrast_ok is True

    @pytest.mark.asyncio
    async def test_failing_contract(self):
        vlm_result = {
            "contrast_ok": False,
            "readability_ok": True,
            "aesthetic_ok": False,
            "issues": ["Low contrast on muted text"],
            "suggested_adjustments": ["Darken text_muted to #666"],
        }

        from clawdbot.design_tokens import DesignTokens

        tokens = DesignTokens()
        mock_pool = AsyncMock()

        mock_render = AsyncMock(return_value=FAKE_PNG)
        with (
            patch("clawdbot.renderer.render_section_in_page", mock_render),
            patch("clawdbot.visual_scorer._vlm_score", return_value=vlm_result),
        ):
            result = await validate_design_contract(tokens, mock_pool)

        assert result.passed is False
        assert len(result.issues) == 1
        assert len(result.suggested_adjustments) == 1
