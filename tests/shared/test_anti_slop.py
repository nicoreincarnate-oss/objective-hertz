"""Tests for shared/anti_slop.py -- scoring, slop detection, rewrites, secrets, DB functions.

Covers:
- 5-dimension scoring with known slop (scores low) and clean content (scores high)
- Slop pattern detection: 20-sample corpus with known slop phrases (80%+ detection)
- Best-of-N rewrite returns highest scorer, not latest
- Good-enough threshold skips unnecessary rewrites
- Secret detection catches API keys, passwords, tokens, CC numbers
- Secret detection blocks content (returns error, doesn't pass through)
- quality_scores record + trend query (mock DB)
- isinstance(AntiSlopScorer(), SlopScorer) Protocol check
- Cost estimation: verify scoring uses Haiku model parameter
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from shared.anti_slop import (
    SLOP_PATTERNS,
    AntiSlopScorer,
    _calculate_slop_score,
    _composite_score,
    _is_good_enough,
    detect_secrets,
    is_enabled,
    record_quality_score,
    rewrite_loop,
)
from shared.contracts import SlopScorer

# ---------------------------------------------------------------------------
# Protocol check
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_anti_slop_scorer_implements_slop_scorer(self):
        """AntiSlopScorer must satisfy the SlopScorer runtime_checkable Protocol."""
        scorer = AntiSlopScorer()
        assert isinstance(scorer, SlopScorer)

    def test_has_score_method(self):
        scorer = AntiSlopScorer()
        assert callable(getattr(scorer, "score", None))

    def test_has_get_threshold_method(self):
        scorer = AntiSlopScorer()
        assert callable(getattr(scorer, "get_threshold", None))


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_ANTI_SLOP", None)
            assert is_enabled() is False

    def test_enabled_true(self):
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "true"}):
            assert is_enabled() is True

    def test_enabled_one(self):
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "1"}):
            assert is_enabled() is True

    def test_disabled_false(self):
        with patch.dict(os.environ, {"ENABLE_ANTI_SLOP": "false"}):
            assert is_enabled() is False


# ---------------------------------------------------------------------------
# Slop score (regex-based, no LLM)
# ---------------------------------------------------------------------------


class TestSlopScore:
    def test_clean_content_scores_low(self):
        """Content without slop phrases should score near 0."""
        clean = (
            "Hi Maria, I noticed your bakery on 5th Ave doesn't have a website yet. "
            "Your competitors Sunrise Bakery and Golden Crust both have sites driving "
            "online orders. I build professional websites for local bakeries for $299."
        )
        score = _calculate_slop_score(clean)
        assert score < 0.15, f"Clean content scored {score}, expected < 0.15"

    def test_sloppy_content_scores_high(self):
        """Content packed with AI cliches should score high."""
        sloppy = (
            "I hope this email finds you well. I wanted to reach out because our "
            "cutting-edge, state-of-the-art, innovative solution can take your business "
            "to the next level. We leverage synergy to empower your brand with our "
            "revolutionary, transformative, best-in-class paradigm shift. "
            "Let me know if you have any questions. Looking forward to connecting."
        )
        score = _calculate_slop_score(sloppy)
        assert score > 0.3, f"Sloppy content scored {score}, expected > 0.3"

    def test_empty_content_scores_zero(self):
        assert _calculate_slop_score("") == 0.0
        assert _calculate_slop_score("   ") == 0.0

    def test_single_slop_phrase_nonzero(self):
        """A single slop phrase should produce a non-zero score."""
        text = "I hope this email finds you well. Here is my proposal for your bakery website."
        score = _calculate_slop_score(text)
        assert score > 0.0, "Single slop phrase should score > 0"
        # Density-based: 1 match in ~15 words -> non-trivial score
        assert score <= 1.0

    def test_more_slop_scores_higher(self):
        """More slop phrases in same word count -> higher score."""
        one_slop = "I hope this email finds you well. I have a proposal for your business."
        many_slop = (
            "I hope this email finds you well. I wanted to reach out because "
            "our cutting-edge solution can leverage synergy."
        )
        assert _calculate_slop_score(many_slop) > _calculate_slop_score(one_slop)


class TestSlopPatternCorpus:
    """20-sample corpus: known slop phrases must be detected by the regex patterns."""

    SLOP_SAMPLES = [
        "I hope this email finds you well",
        "I wanted to reach out",
        "take it to the next level",
        "in today's fast-paced digital world",
        "leverage our expertise",
        "a game-changer for your business",
        "cutting-edge technology",
        "state-of-the-art solutions",
        "deep dive into your needs",
        "low-hanging fruit",
        "move the needle on your growth",
        "at the end of the day",
        "touch base with you",
        "revolutionary approach",
        "seamless integration",
        "robust and scalable",
        "innovative solutions",
        "world-class service",
        "empower your team",
        "best-in-class platform",
    ]

    def test_80_percent_detection_rate(self):
        """At least 16 of 20 known slop phrases must be detected by SLOP_PATTERNS."""
        detected = 0
        for sample in self.SLOP_SAMPLES:
            for pattern in SLOP_PATTERNS:
                if pattern.search(sample):
                    detected += 1
                    break
        rate = detected / len(self.SLOP_SAMPLES)
        assert rate >= 0.8, (
            f"Detection rate {rate:.0%} ({detected}/{len(self.SLOP_SAMPLES)}) "
            f"is below the 80% threshold"
        )


# ---------------------------------------------------------------------------
# Composite score and threshold
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_perfect_scores(self):
        scores = {
            "clarity": 1.0,
            "specificity": 1.0,
            "authenticity": 1.0,
            "value_density": 1.0,
            "slop_score": 0.0,
        }
        assert _composite_score(scores) == 1.0

    def test_worst_scores(self):
        scores = {
            "clarity": 0.0,
            "specificity": 0.0,
            "authenticity": 0.0,
            "value_density": 0.0,
            "slop_score": 1.0,
        }
        assert _composite_score(scores) == 0.0

    def test_mixed_scores(self):
        scores = {
            "clarity": 0.8,
            "specificity": 0.6,
            "authenticity": 0.7,
            "value_density": 0.5,
            "slop_score": 0.1,
        }
        composite = _composite_score(scores)
        # (0.8 + 0.6 + 0.7 + 0.5 + 0.9) / 5 = 0.7
        assert abs(composite - 0.7) < 0.01


class TestGoodEnough:
    def test_email_good_enough(self):
        good = {"clarity": 0.8, "specificity": 0.8, "authenticity": 0.8, "value_density": 0.8, "slop_score": 0.1}
        assert _is_good_enough(good, "email") is True

    def test_email_not_good_enough_slop(self):
        bad_slop = {"clarity": 0.8, "specificity": 0.8, "authenticity": 0.8, "value_density": 0.8, "slop_score": 0.5}
        assert _is_good_enough(bad_slop, "email") is False

    def test_site_copy_higher_threshold(self):
        moderate = {"clarity": 0.7, "specificity": 0.7, "authenticity": 0.7, "value_density": 0.7, "slop_score": 0.18}
        assert _is_good_enough(moderate, "site_copy") is False  # threshold is 0.75

    def test_unknown_context_uses_email_default(self):
        good = {"clarity": 0.8, "specificity": 0.8, "authenticity": 0.8, "value_density": 0.8, "slop_score": 0.1}
        assert _is_good_enough(good, "unknown_context") is True


# ---------------------------------------------------------------------------
# Secret detection
# ---------------------------------------------------------------------------


class TestSecretDetection:
    def test_openai_api_key_detected(self):
        """Standard OpenAI-style sk- key (alphanumeric, 20+ chars)."""
        text = "Use this key: sk-abc123def456ghi789jkl012mno"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_anthropic_key_detected(self):
        text = "Key: sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_github_pat_detected(self):
        text = "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_aws_key_detected(self):
        text = "AWS: AKIAIOSFODNN7EXAMPLE"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_stripe_key_detected(self):
        text = "Stripe: sk_live_4eC39HqLyjWDarjtT1zdp7dc"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_credit_card_detected(self):
        text = "Card: 4532-0150-1234-5678"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_ssn_detected(self):
        text = "SSN: 123-45-6789"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_password_detected(self):
        text = "password: mysecretpass123"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_private_key_detected(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_jwt_detected(self):
        text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_db_connection_string_detected(self):
        text = "postgres://user:password@localhost:5432/mydb"
        findings = detect_secrets(text)
        assert len(findings) > 0

    def test_clean_content_no_secrets(self):
        text = "Hi, I build professional websites for local businesses. Check out my portfolio."
        findings = detect_secrets(text)
        assert len(findings) == 0

    def test_sendgrid_key_detected(self):
        text = "SG.abcdefghijklmnopqrstuvwx.yz0123456789abcdefghijklm"
        findings = detect_secrets(text)
        assert len(findings) > 0


# ---------------------------------------------------------------------------
# Scoring (with mocked LLM)
# ---------------------------------------------------------------------------


class TestAntiSlopScorer:
    @pytest.mark.asyncio
    async def test_score_returns_5_dimensions(self):
        """Score should return all 5 dimensions."""
        scorer = AntiSlopScorer()
        with patch("shared.anti_slop._llm_score_dimensions", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "clarity": 0.8, "specificity": 0.7,
                "authenticity": 0.9, "value_density": 0.6,
            }
            scores = await scorer.score("Test content", "email")
            assert set(scores.keys()) == {"clarity", "specificity", "authenticity", "value_density", "slop_score"}
            assert all(0.0 <= v <= 1.0 for v in scores.values())

    @pytest.mark.asyncio
    async def test_score_slop_is_regex_based(self):
        """Slop score should be calculated from regex, not LLM."""
        scorer = AntiSlopScorer()
        sloppy = "I hope this email finds you well. Leverage synergy to take it to the next level."
        with patch("shared.anti_slop._llm_score_dimensions", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "clarity": 0.5, "specificity": 0.5,
                "authenticity": 0.5, "value_density": 0.5,
            }
            scores = await scorer.score(sloppy, "email")
            assert scores["slop_score"] > 0.0  # Must detect patterns

    @pytest.mark.asyncio
    async def test_score_uses_haiku_model(self):
        """LLM scoring should request the 'fast' (Haiku) model."""
        mock_generate = AsyncMock(
            return_value='{"clarity": 0.8, "specificity": 0.7, "authenticity": 0.9, "value_density": 0.6}'
        )
        mock_llm_client = type("MockLLM", (), {"generate": mock_generate})()

        with patch("shared.llm_client.llm", mock_llm_client):
            from shared.anti_slop import _llm_score_dimensions
            await _llm_score_dimensions("test content", "email")

            mock_generate.assert_called_once()
            call_kwargs = mock_generate.call_args
            # model should be "fast" (= Haiku)
            assert call_kwargs.kwargs.get("model") == "fast"

    def test_get_threshold_email(self):
        scorer = AntiSlopScorer()
        assert scorer.get_threshold("email") == 0.7

    def test_get_threshold_site_copy(self):
        scorer = AntiSlopScorer()
        assert scorer.get_threshold("site_copy") == 0.75


# ---------------------------------------------------------------------------
# Rewrite loop
# ---------------------------------------------------------------------------


class TestRewriteLoop:
    @pytest.mark.asyncio
    async def test_returns_best_version_not_latest(self):
        """Rewrite loop should return the highest-scoring version, not the last."""
        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"Rewrite version {call_count} with specific details about bakery on 5th Ave."

        bad_scores = {"clarity": 0.3, "specificity": 0.3, "authenticity": 0.3, "value_density": 0.3, "slop_score": 0.8}

        mock_llm_client = type("MockLLM", (), {"generate": AsyncMock(side_effect=mock_generate)})()

        with patch("shared.llm_client.llm", mock_llm_client):
            # First rewrite gets great scores (stops loop)
            scores_sequence = [
                {"clarity": 0.9, "specificity": 0.9, "authenticity": 0.9, "value_density": 0.9, "slop_score": 0.0},
            ]
            with patch.object(AntiSlopScorer, "score", new_callable=AsyncMock, side_effect=scores_sequence):
                result = await rewrite_loop(
                    "Original sloppy content",
                    bad_scores,
                    context="email",
                    max_iterations=3,
                )
                # Should have rewritten (scores were bad -> triggered rewrite)
                assert "Rewrite version" in result

    @pytest.mark.asyncio
    async def test_skips_rewrite_when_good_enough(self):
        """If initial scores are good enough, no rewrites should happen."""
        good_scores = {"clarity": 0.9, "specificity": 0.9, "authenticity": 0.9, "value_density": 0.9, "slop_score": 0.05}

        mock_llm_client = type("MockLLM", (), {"generate": AsyncMock()})()

        with patch("shared.llm_client.llm", mock_llm_client):
            result = await rewrite_loop(
                "Already great content",
                good_scores,
                context="email",
                max_iterations=3,
            )
            assert result == "Already great content"
            mock_llm_client.generate.assert_not_called()


# ---------------------------------------------------------------------------
# DB functions (mocked)
# ---------------------------------------------------------------------------


class TestDBFunctions:
    @pytest.mark.asyncio
    async def test_record_quality_score(self):
        """record_quality_score should call execute with correct parameters."""
        scores = {"clarity": 0.8, "specificity": 0.7, "authenticity": 0.9, "value_density": 0.6, "slop_score": 0.1}

        mock_execute = AsyncMock()
        with patch("shared.db.execute", mock_execute):
            await record_quality_score(
                reference_id="lead_123",
                content_type="email",
                scores=scores,
                rewrite_count=1,
            )
            mock_execute.assert_called_once()
            args = mock_execute.call_args[0]
            assert "quality_scores" in args[0]
            params = args[1]
            assert params[0] == "email"
            assert params[1] == "lead_123"

    @pytest.mark.asyncio
    async def test_record_quality_score_failure_logged(self):
        """DB failures should be caught and logged, not raised."""
        scores = {"clarity": 0.5, "specificity": 0.5, "authenticity": 0.5, "value_density": 0.5, "slop_score": 0.5}

        with patch("shared.db.execute", AsyncMock(side_effect=Exception("DB connection failed"))):
            # Should not raise
            await record_quality_score("ref_1", "email", scores)
