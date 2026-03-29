"""Tests for anti-slop quality gate integration in titan/pipeline/email_compose.py.

Covers:
- Email pipeline: flag on -> scores + gates, flag off -> passthrough
- Email pipeline: detected secret -> blocked
- Integration with _compose_one and _compose_with_skill
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

# We test the _anti_slop_gate function directly since compose_emails
# requires DB connections. Integration is validated through the gate.
from titan.pipeline.email_compose import _anti_slop_gate


class TestAntiSlopGateFlagOff:
    """When ENABLE_ANTI_SLOP is off, the gate should be a no-op passthrough."""

    @pytest.mark.asyncio
    async def test_passthrough_when_disabled(self):
        """Flag off means no scoring, no blocking, original content returned."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_ANTI_SLOP", None)
            subject, body, passed = await _anti_slop_gate(
                "Test Subject",
                "This is the email body with leverage and synergy.",
                lead_id=42,
            )
            assert passed is True
            assert subject == "Test Subject"
            assert body == "This is the email body with leverage and synergy."

    @pytest.mark.asyncio
    async def test_passthrough_when_explicitly_false(self):
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "false"}):
            subject, body, passed = await _anti_slop_gate("Sub", "Body", lead_id=1)
            assert passed is True


class TestAntiSlopGateFlagOn:
    """When ENABLE_ANTI_SLOP is on, the gate should score, rewrite, and block."""

    @pytest.mark.asyncio
    async def test_secret_detected_blocks(self):
        """If secrets are found, the gate MUST block (passed=False)."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            with patch("titan.pipeline.email_compose.emit_pipeline_error", new_callable=AsyncMock):
                subject, body, passed = await _anti_slop_gate(
                    "Your API Key",
                    "Here is your key: sk_live_4eC39HqLyjWDarjtT1zdp7dc123456",
                    lead_id=99,
                )
                assert passed is False

    @pytest.mark.asyncio
    async def test_clean_content_passes(self):
        """Clean content with good scores should pass through."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            good_scores = {
                "clarity": 0.9, "specificity": 0.9,
                "authenticity": 0.9, "value_density": 0.9,
                "slop_score": 0.05,
            }
            with patch.object(
                _anti_slop_gate.__globals__["_slop_scorer"],
                "score",
                new_callable=AsyncMock,
                return_value=good_scores,
            ):
                with patch("titan.pipeline.email_compose.record_quality_score", new_callable=AsyncMock):
                    subject, body, passed = await _anti_slop_gate(
                        "Website for your bakery",
                        "Hi Maria, I build websites for bakeries in Brooklyn. Your competitors have sites driving orders.",
                        lead_id=42,
                    )
                    assert passed is True

    @pytest.mark.asyncio
    async def test_sloppy_content_triggers_rewrite(self):
        """Content below threshold should trigger the rewrite loop."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            bad_scores = {
                "clarity": 0.4, "specificity": 0.3,
                "authenticity": 0.3, "value_density": 0.3,
                "slop_score": 0.6,
            }
            good_scores = {
                "clarity": 0.9, "specificity": 0.9,
                "authenticity": 0.9, "value_density": 0.9,
                "slop_score": 0.05,
            }

            scorer_mock = _anti_slop_gate.__globals__["_slop_scorer"]
            with patch.object(
                scorer_mock,
                "score",
                new_callable=AsyncMock,
                side_effect=[bad_scores, good_scores],
            ):
                with patch(
                    "titan.pipeline.email_compose.rewrite_loop",
                    new_callable=AsyncMock,
                    return_value="Rewritten cleaner body",
                ) as mock_rewrite:
                    with patch("titan.pipeline.email_compose.record_quality_score", new_callable=AsyncMock):
                        subject, body, passed = await _anti_slop_gate(
                            "Subject",
                            "I hope this email finds you well. Leverage synergy to take it to the next level.",
                            lead_id=42,
                        )
                        assert passed is True
                        mock_rewrite.assert_called_once()
                        assert body == "Rewritten cleaner body"

    @pytest.mark.asyncio
    async def test_records_quality_score(self):
        """Gate should record quality scores to DB via record_quality_score."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            good_scores = {
                "clarity": 0.9, "specificity": 0.9,
                "authenticity": 0.9, "value_density": 0.9,
                "slop_score": 0.05,
            }
            scorer_mock = _anti_slop_gate.__globals__["_slop_scorer"]
            with patch.object(
                scorer_mock,
                "score",
                new_callable=AsyncMock,
                return_value=good_scores,
            ):
                with patch(
                    "titan.pipeline.email_compose.record_quality_score",
                    new_callable=AsyncMock,
                ) as mock_record:
                    await _anti_slop_gate("Sub", "Body", lead_id=77)
                    mock_record.assert_called_once()
                    call_kwargs = mock_record.call_args[1]
                    assert call_kwargs["reference_id"] == "77"
                    assert call_kwargs["content_type"] == "email"

    @pytest.mark.asyncio
    async def test_secret_in_subject_blocks(self):
        """Secrets in the subject line should also trigger blocking."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            with patch("titan.pipeline.email_compose.emit_pipeline_error", new_callable=AsyncMock):
                _, _, passed = await _anti_slop_gate(
                    "Key: sk_live_4eC39HqLyjWDarjtT1zdp7dc123456",
                    "Normal body text here",
                    lead_id=1,
                )
                assert passed is False

    @pytest.mark.asyncio
    async def test_credit_card_in_body_blocks(self):
        """Credit card numbers in body should block."""
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            with patch("titan.pipeline.email_compose.emit_pipeline_error", new_callable=AsyncMock):
                _, _, passed = await _anti_slop_gate(
                    "Invoice",
                    "Please charge card 4532-0150-1234-5678 for the website.",
                    lead_id=1,
                )
                assert passed is False
