"""Tests for titan/neuro/learning_loop.py — closed-loop neural learning.

Covers:
- compute_correlations with synthetic data
- Pearson correlation correctness (numpy fallback)
- neural_reflection with mock DB data
- Rule extraction at significance threshold (p<0.05, n>=30)
- Weight update logic
- compute_segment_profiles with mock segments
- Edge cases: insufficient data, no signal, NaN handling

ALL tests are mocked -- no DB or torch required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from titan.neuro.learning_loop import (
    _DIMENSIONS,
    _pearsonr,
    _update_composite_weights,
    compute_correlations,
    compute_segment_profiles,
    neural_reflection,
)

# ---------------------------------------------------------------------------
# Pearson correlation tests
# ---------------------------------------------------------------------------


class TestPearsonr:
    """Test the _pearsonr helper (numpy fallback)."""

    def test_perfect_positive_correlation(self):
        """r=1.0 for perfectly correlated data."""
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        r, p = _pearsonr(x, y)
        assert abs(r - 1.0) < 0.01
        assert p < 0.05

    def test_perfect_negative_correlation(self):
        """r=-1.0 for perfectly anti-correlated data."""
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [10.0, 8.0, 6.0, 4.0, 2.0]
        r, p = _pearsonr(x, y)
        assert abs(r - (-1.0)) < 0.01

    def test_no_correlation(self):
        """Near-zero r for random/uncorrelated data."""
        np.random.seed(42)
        x = list(np.random.randn(200))
        y = list(np.random.randn(200))
        r, _p = _pearsonr(x, y)
        assert abs(r) < 0.2  # Should be close to 0

    def test_short_input(self):
        """Returns (0.0, 1.0) for inputs shorter than 3."""
        r, p = _pearsonr([1.0], [2.0])
        assert r == 0.0
        assert p == 1.0

    def test_constant_values(self):
        """Handles constant values (NaN guard)."""
        x = [5.0, 5.0, 5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0, 4.0, 5.0]
        r, p = _pearsonr(x, y)
        # NaN should be caught and return 0.0
        assert r == 0.0
        assert p == 1.0


# ---------------------------------------------------------------------------
# compute_correlations tests
# ---------------------------------------------------------------------------


class TestComputeCorrelations:
    """Test compute_correlations helper (no DB required)."""

    def test_basic_correlation(self):
        """Compute correlations for 4 dimensions against binary outcomes."""
        n = 50
        np.random.seed(42)

        # Make self_relevance correlate with outcomes
        outcomes = [1 if i > 25 else 0 for i in range(n)]
        scores_by_dim = {
            "self_relevance": [0.3 + 0.4 * o + np.random.randn() * 0.1 for o in outcomes],
            "trust": list(np.random.rand(n)),
            "cognitive_ease": list(np.random.rand(n)),
            "emotional_resonance": list(np.random.rand(n)),
        }

        corrs = compute_correlations(scores_by_dim, outcomes)

        assert len(corrs) == 4
        for dim in _DIMENSIONS:
            assert "r" in corrs[dim]
            assert "p" in corrs[dim]
            assert "n" in corrs[dim]
            assert corrs[dim]["n"] == n

        # self_relevance should have positive correlation
        assert corrs["self_relevance"]["r"] > 0.3

    def test_insufficient_data(self):
        """Returns zero correlation for fewer than 3 observations."""
        scores_by_dim = {
            "self_relevance": [0.5, 0.6],
            "trust": [0.4, 0.5],
            "cognitive_ease": [0.6, 0.7],
            "emotional_resonance": [0.3, 0.4],
        }
        outcomes = [1, 0]

        corrs = compute_correlations(scores_by_dim, outcomes)
        for dim in _DIMENSIONS:
            assert corrs[dim]["r"] == 0.0
            assert corrs[dim]["n"] == 0

    def test_mismatched_lengths(self):
        """Returns zero when dimension data length != outcomes length."""
        scores_by_dim = {
            "self_relevance": [0.5, 0.6, 0.7],
            "trust": [0.4, 0.5],  # Wrong length
            "cognitive_ease": [0.6, 0.7, 0.8],
            "emotional_resonance": [0.3, 0.4, 0.5],
        }
        outcomes = [1, 0, 1]

        corrs = compute_correlations(scores_by_dim, outcomes)
        assert corrs["trust"]["r"] == 0.0
        assert corrs["trust"]["n"] == 0


# ---------------------------------------------------------------------------
# neural_reflection tests (mocked DB)
# ---------------------------------------------------------------------------


class TestNeuralReflection:
    """Test daily neural reflection with mocked DB."""

    @pytest.mark.asyncio
    async def test_insufficient_data(self):
        """Returns insufficient_data when fewer than 50 scored emails."""
        mock_data = [
            {"neuro_scores": {"self_relevance": 0.5, "trust": 0.5, "cognitive_ease": 0.5, "emotional_resonance": 0.5}, "converted": 1}
            for _ in range(30)
        ]
        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_data):
            result = await neural_reflection()
            assert result["status"] == "insufficient_data"
            assert result["count"] == 30

    @pytest.mark.asyncio
    async def test_complete_with_correlations(self):
        """Returns correlations when enough data exists."""
        n = 80
        np.random.seed(42)
        mock_data = []
        for i in range(n):
            converted = 1 if i > 40 else 0
            mock_data.append({
                "neuro_scores": {
                    "self_relevance": 0.3 + 0.4 * converted + np.random.randn() * 0.05,
                    "trust": np.random.rand(),
                    "cognitive_ease": np.random.rand(),
                    "emotional_resonance": np.random.rand(),
                },
                "converted": converted,
                "industry": "dental",
                "lead_score": 70,
            })

        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_data):
            with patch("shared.db.execute", new_callable=AsyncMock):
                with patch("shared.db.set_config", new_callable=AsyncMock):
                    result = await neural_reflection()

        assert result["status"] == "complete"
        assert "correlations" in result
        assert "self_relevance" in result["correlations"]

    @pytest.mark.asyncio
    async def test_query_error(self):
        """Returns query_error when DB query fails."""
        with patch("shared.db.fetch_all", new_callable=AsyncMock, side_effect=Exception("connection refused")):
            result = await neural_reflection()
            assert result["status"] == "query_error"

    @pytest.mark.asyncio
    async def test_rules_created_at_significance(self):
        """Auto-creates rules when p < 0.05 and n >= 30."""
        n = 100
        np.random.seed(123)
        mock_data = []
        for i in range(n):
            converted = 1 if i > 50 else 0
            mock_data.append({
                "neuro_scores": {
                    "self_relevance": 0.2 + 0.6 * converted + np.random.randn() * 0.05,
                    "trust": np.random.rand(),
                    "cognitive_ease": np.random.rand(),
                    "emotional_resonance": np.random.rand(),
                },
                "converted": converted,
                "industry": "dental",
                "lead_score": 70,
            })

        mock_execute = AsyncMock()
        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_data):
            with patch("shared.db.execute", mock_execute):
                with patch("shared.db.set_config", new_callable=AsyncMock):
                    result = await neural_reflection()

        assert result["status"] == "complete"
        # self_relevance should be significant and trigger rule creation
        assert "self_relevance" in result.get("rules_created", [])
        # execute should have been called to insert the rule
        assert mock_execute.await_count >= 1


# ---------------------------------------------------------------------------
# Weight update tests
# ---------------------------------------------------------------------------


class TestWeightUpdate:
    """Test composite weight update logic."""

    @pytest.mark.asyncio
    async def test_no_update_when_no_signal(self):
        """No weight update when all p-values > 0.1."""
        correlations = {
            "self_relevance": {"r": 0.05, "p": 0.6, "n": 50},
            "trust": {"r": -0.03, "p": 0.8, "n": 50},
            "cognitive_ease": {"r": 0.02, "p": 0.9, "n": 50},
            "emotional_resonance": {"r": 0.01, "p": 0.95, "n": 50},
        }
        mock_set = AsyncMock()
        with patch("shared.db.set_config", mock_set):
            await _update_composite_weights(correlations)

        # Should not update since no signal
        mock_set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_shifts_toward_predictive(self):
        """Weights shift toward dimensions with strong correlation."""
        correlations = {
            "self_relevance": {"r": 0.8, "p": 0.001, "n": 100},
            "trust": {"r": 0.1, "p": 0.4, "n": 100},
            "cognitive_ease": {"r": 0.3, "p": 0.05, "n": 100},
            "emotional_resonance": {"r": 0.05, "p": 0.7, "n": 100},
        }
        mock_set = AsyncMock()
        with patch("shared.db.set_config", mock_set):
            await _update_composite_weights(correlations)

        mock_set.assert_awaited_once()
        args = mock_set.call_args[0]
        assert args[0] == "neuro_composite_weights"
        weights = args[1]

        # self_relevance should get the highest weight
        assert weights["self_relevance"] > weights["trust"]
        # Weights should sum to ~1.0
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Segment profile tests
# ---------------------------------------------------------------------------


class TestSegmentProfiles:
    """Test weekly segment profile computation."""

    @pytest.mark.asyncio
    async def test_empty_when_no_data(self):
        """Returns empty dict when no segments meet threshold."""
        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=[]):
            profiles = await compute_segment_profiles()
            assert profiles == {}

    @pytest.mark.asyncio
    async def test_profiles_per_industry(self):
        """Creates profiles for each qualifying industry."""
        mock_segments = [
            {
                "industry": "dental",
                "avg_sr": 0.72,
                "avg_trust": 0.65,
                "avg_ease": 0.58,
                "avg_emo": 0.61,
                "n": 150,
            },
            {
                "industry": "plumbing",
                "avg_sr": 0.68,
                "avg_trust": 0.70,
                "avg_ease": 0.55,
                "avg_emo": 0.59,
                "n": 120,
            },
        ]

        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_segments):
            with patch("shared.db.execute", new_callable=AsyncMock):
                profiles = await compute_segment_profiles()

        assert "dental" in profiles
        assert "plumbing" in profiles
        assert profiles["dental"]["self_relevance"] == 0.72
        assert profiles["plumbing"]["trust"] == 0.70

    @pytest.mark.asyncio
    async def test_profiles_stored_in_titan_learnings(self):
        """Profiles are stored in titan_learnings via execute."""
        mock_segments = [
            {
                "industry": "restaurant",
                "avg_sr": 0.60,
                "avg_trust": 0.55,
                "avg_ease": 0.65,
                "avg_emo": 0.70,
                "n": 200,
            },
        ]

        mock_execute = AsyncMock()
        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_segments):
            with patch("shared.db.execute", mock_execute):
                await compute_segment_profiles()

        # Should store the profile
        assert mock_execute.await_count >= 1
        call_args = mock_execute.call_args[0]
        assert "neural_profile_restaurant" in call_args[1]

    @pytest.mark.asyncio
    async def test_query_error_returns_empty(self):
        """Returns empty dict on query failure."""
        with patch("shared.db.fetch_all", new_callable=AsyncMock, side_effect=Exception("DB error")):
            profiles = await compute_segment_profiles()
            assert profiles == {}


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_pearsonr_with_nan(self):
        """NaN in data handled gracefully."""
        x = [1.0, float("nan"), 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        # Should not raise
        r, p = _pearsonr(x, y)
        # Result might be NaN-guarded to 0.0
        assert isinstance(r, float)
        assert isinstance(p, float)

    def test_dimensions_constant(self):
        """_DIMENSIONS has exactly 4 entries in canonical order."""
        assert len(_DIMENSIONS) == 4
        assert _DIMENSIONS[0] == "self_relevance"
        assert _DIMENSIONS[1] == "trust"
        assert _DIMENSIONS[2] == "cognitive_ease"
        assert _DIMENSIONS[3] == "emotional_resonance"

    @pytest.mark.asyncio
    async def test_reflection_handles_missing_dimension_key(self):
        """Reflection handles rows where neuro_scores is missing a dimension."""
        mock_data = [
            {
                "neuro_scores": {"self_relevance": 0.5},  # Missing other dims
                "converted": 1,
                "industry": "dental",
                "lead_score": 70,
            }
            for _ in range(60)
        ]

        with patch("shared.db.fetch_all", new_callable=AsyncMock, return_value=mock_data):
            with patch("shared.db.execute", new_callable=AsyncMock):
                with patch("shared.db.set_config", new_callable=AsyncMock):
                    result = await neural_reflection()

        # Should complete but some dimensions will have zero correlation
        assert result["status"] == "complete"
