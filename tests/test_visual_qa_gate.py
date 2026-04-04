"""Tests for the visual QA gate module."""

import asyncio
import base64
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from tools.visual_qa_gate import (
    QAResult,
    SCORING_DIMENSIONS,
    PASS_THRESHOLD,
    REGENERATE_THRESHOLD,
    MAX_REGENERATION_ATTEMPTS,
    evaluate_site,
    run_qa_loop,
    score_with_vision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal PNG-like bytes


def _make_scores(value: float) -> dict[str, float]:
    """Return a scores dict with every dimension set to *value*."""
    return {dim: value for dim in SCORING_DIMENSIONS}


def _make_screenshots() -> dict[str, bytes]:
    return {"desktop": FAKE_PNG, "tablet": FAKE_PNG, "mobile": FAKE_PNG}


# ---------------------------------------------------------------------------
# test_qa_result_dataclass
# ---------------------------------------------------------------------------

def test_qa_result_dataclass():
    r = QAResult(passed=True, average_score=8.0)
    assert r.passed is True
    assert r.average_score == 8.0
    assert r.scores == {}
    assert r.feedback == ""
    assert r.screenshots == {}
    assert r.action == ""
    assert r.attempt == 0

    r2 = QAResult(
        passed=False,
        average_score=4.5,
        scores={"visual_hierarchy": 4.5},
        feedback="needs work",
        action="escalate",
        attempt=2,
    )
    assert r2.scores == {"visual_hierarchy": 4.5}
    assert r2.action == "escalate"


# ---------------------------------------------------------------------------
# test_pass_threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pass_threshold():
    """Average >= 7.0 -> action='pass', passed=True."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", new_callable=AsyncMock) as mock_score:
        mock_cap.return_value = _make_screenshots()
        mock_score.return_value = (_make_scores(8.0), "Looks great")

        result = await evaluate_site("http://example.com", attempt=0)

        assert result.passed is True
        assert result.action == "pass"
        assert result.average_score == 8.0


# ---------------------------------------------------------------------------
# test_regenerate_threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_regenerate_threshold():
    """Average 5.0-6.9 on attempt 0 -> action='regenerate'."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", new_callable=AsyncMock) as mock_score:
        mock_cap.return_value = _make_screenshots()
        mock_score.return_value = (_make_scores(6.0), "Needs polish")

        result = await evaluate_site("http://example.com", attempt=0)

        assert result.passed is False
        assert result.action == "regenerate"
        assert 5.0 <= result.average_score < 7.0


# ---------------------------------------------------------------------------
# test_escalate_threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escalate_threshold():
    """Average < 5.0 -> action='escalate'."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", new_callable=AsyncMock) as mock_score:
        mock_cap.return_value = _make_screenshots()
        mock_score.return_value = (_make_scores(3.0), "Major issues")

        result = await evaluate_site("http://example.com", attempt=0)

        assert result.passed is False
        assert result.action == "escalate"
        assert result.average_score < 5.0


# ---------------------------------------------------------------------------
# test_max_regeneration_attempts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_regeneration_attempts():
    """When attempt >= MAX_REGENERATION_ATTEMPTS and score is in regenerate band, escalate."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", new_callable=AsyncMock) as mock_score:
        mock_cap.return_value = _make_screenshots()
        mock_score.return_value = (_make_scores(6.0), "Still not great")

        # attempt=2 means we've already used up both regeneration slots
        result = await evaluate_site("http://example.com", attempt=MAX_REGENERATION_ATTEMPTS)

        assert result.passed is False
        assert result.action == "escalate"


# ---------------------------------------------------------------------------
# test_no_screenshots_escalates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_screenshots_escalates():
    """If no screenshots captured, escalate immediately."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap:
        mock_cap.return_value = {}

        result = await evaluate_site("http://example.com")

        assert result.passed is False
        assert result.action == "escalate"
        assert result.average_score == 0.0
        assert "screenshot" in result.feedback.lower()


# ---------------------------------------------------------------------------
# test_score_validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_score_validation():
    """Scores outside [1, 10] are clamped."""
    raw_scores = {
        "visual_hierarchy": 15.0,
        "spacing_alignment": -2.0,
        "typography": 0.0,
        "color_harmony": 10.0,
        "component_quality": 1.0,
        "mobile_responsiveness": 5.5,
        "professional_polish": 100.0,
    }

    # Mock the anthropic module so it doesn't need to be installed
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text='{"scores": '
            + str(raw_scores).replace("'", '"')
            + ', "feedback": "clamped"}'
        )
    ]

    mock_client_instance = AsyncMock()
    mock_client_instance.messages.create = AsyncMock(return_value=mock_response)

    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client_instance

    import sys

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}), \
         patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        scores, feedback = await score_with_vision(_make_screenshots())

    assert scores["visual_hierarchy"] == 10.0   # clamped from 15
    assert scores["spacing_alignment"] == 1.0   # clamped from -2
    assert scores["typography"] == 1.0           # clamped from 0
    assert scores["color_harmony"] == 10.0       # already at max
    assert scores["component_quality"] == 1.0    # already at min
    assert scores["mobile_responsiveness"] == 5.5
    assert scores["professional_polish"] == 10.0  # clamped from 100


# ---------------------------------------------------------------------------
# test_qa_loop_passes_first_try
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa_loop_passes_first_try():
    """run_qa_loop returns immediately when first evaluation passes."""
    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", new_callable=AsyncMock) as mock_score:
        mock_cap.return_value = _make_screenshots()
        mock_score.return_value = (_make_scores(8.5), "Excellent")

        callback = AsyncMock(return_value="http://new.example.com")
        result = await run_qa_loop("http://example.com", regenerate_callback=callback)

        assert result.passed is True
        assert result.action == "pass"
        callback.assert_not_called()


# ---------------------------------------------------------------------------
# test_qa_loop_regenerates_then_passes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa_loop_regenerates_then_passes():
    """run_qa_loop regenerates once, then passes on second attempt."""
    call_count = 0

    async def _mock_score(screenshots):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_scores(6.0), "Needs work on spacing"
        return _make_scores(8.0), "Much better"

    with patch("tools.visual_qa_gate.capture_screenshots", new_callable=AsyncMock) as mock_cap, \
         patch("tools.visual_qa_gate.score_with_vision", side_effect=_mock_score):
        mock_cap.return_value = _make_screenshots()

        callback = AsyncMock(return_value="http://regenerated.example.com")
        result = await run_qa_loop("http://example.com", regenerate_callback=callback)

        assert result.passed is True
        assert result.action == "pass"
        assert result.average_score == 8.0
        callback.assert_called_once()
        assert "spacing" in callback.call_args[0][0].lower()
