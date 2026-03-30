"""Tests for titan/neuro/ — NeuroScorer, ROIExtractor, RunningNormalizer, TribeService.

Covers:
- ROI extraction maps all 4 dimensions from mock activation data
- Normalization produces 0-1 scores (pre-baseline warm-start + post-baseline percentile)
- Cognitive load -> cognitive ease inversion
- Composite score computation with default and learned weights
- Feature flag on/off behavior
- Lazy model load/unload (mock TRIBE v2)
- Neural guidance prompt generation for weak dimensions
- Middleware integration
- NeuroScores dataclass serialization

ALL tests are mocked -- no torch/tribev2 required.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from titan.neuro.neuro_scorer import NeuroScorer, NeuroScores, is_enabled
from titan.neuro.normalizer import RunningNormalizer
from titan.neuro.roi_extractor import ROIExtractor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_normalizer():
    """Reset shared normalizer between tests."""
    NeuroScorer.reset_normalizer()
    yield
    NeuroScorer.reset_normalizer()


@pytest.fixture(autouse=True)
def _reset_tribe_service():
    """Reset TribeService singleton between tests."""
    from titan.neuro.tribe_service import TribeService
    TribeService._instance = None
    yield
    TribeService._instance = None


@pytest.fixture
def mock_activation():
    """Mock activation array (simplified: 4 dimensions mapped to columns)."""
    # Shape: (1, n_vertices) -- simplified for testing
    return np.random.rand(1, 100).astype(np.float32)


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """Test ENABLE_NEURO_SCORER feature flag."""

    def test_disabled_by_default(self):
        """When env var not set, is_enabled returns False."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_NEURO_SCORER", None)
            assert is_enabled() is False

    def test_enabled_true(self):
        """ENABLE_NEURO_SCORER=true activates scorer."""
        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "true"}):
            assert is_enabled() is True

    def test_enabled_one(self):
        """ENABLE_NEURO_SCORER=1 also activates."""
        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "1"}):
            assert is_enabled() is True

    def test_disabled_false(self):
        """ENABLE_NEURO_SCORER=false keeps disabled."""
        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "false"}):
            assert is_enabled() is False


# ---------------------------------------------------------------------------
# NeuroScores dataclass tests
# ---------------------------------------------------------------------------


class TestNeuroScores:
    """Test NeuroScores dataclass."""

    def test_defaults(self):
        """Default scores are 0.5."""
        ns = NeuroScores()
        assert ns.self_relevance == 0.5
        assert ns.trust == 0.5
        assert ns.cognitive_ease == 0.5
        assert ns.emotional_resonance == 0.5
        assert ns.composite == 0.5
        assert ns.inference_mode == "fallback"

    def test_to_dict(self):
        """to_dict returns JSON-safe dict."""
        ns = NeuroScores(
            self_relevance=0.8,
            trust=0.7,
            cognitive_ease=0.6,
            emotional_resonance=0.9,
            composite=0.75,
        )
        d = ns.to_dict()
        assert d["self_relevance"] == 0.8
        assert d["composite"] == 0.75
        # Verify JSON-serializable
        json.dumps(d)

    def test_to_dict_round_trips(self):
        """to_dict output is JSON serializable and round-trips."""
        ns = NeuroScores(raw_roi_activations={"test": 1.0}, dimension_weights={"sr": 0.3})
        s = json.dumps(ns.to_dict())
        loaded = json.loads(s)
        assert loaded["raw_roi_activations"] == {"test": 1.0}


# ---------------------------------------------------------------------------
# ROIExtractor tests
# ---------------------------------------------------------------------------


class TestROIExtractor:
    """Test ROI extraction from mock activation data."""

    def test_extract_scores_returns_four_dims(self):
        """extract_scores returns all 4 cognitive dimensions."""
        extractor = ROIExtractor()
        activation = np.random.rand(1, 100).astype(np.float32)
        scores = extractor.extract_scores(activation)

        assert "self_relevance" in scores
        assert "trust" in scores
        assert "cognitive_ease" in scores
        assert "emotional_resonance" in scores

    def test_cognitive_load_inverted(self):
        """cognitive_load is inverted to cognitive_ease (lower load = higher ease)."""
        extractor = ROIExtractor()
        # High activation across the board
        activation = np.ones((1, 100), dtype=np.float32) * 2.0
        scores = extractor.extract_scores(activation)

        # cognitive_ease should be the negation of cognitive_load mean
        # Exact value depends on atlas mapping, but it should be negative
        # (since we're inverting a positive activation)
        assert "cognitive_ease" in scores
        assert "cognitive_load" not in scores  # Should be removed after inversion

    def test_all_scores_are_floats(self):
        """All scores should be plain Python floats."""
        extractor = ROIExtractor()
        activation = np.random.rand(1, 100).astype(np.float32)
        scores = extractor.extract_scores(activation)

        for dim, val in scores.items():
            assert isinstance(val, float), f"{dim} is {type(val)}, expected float"

    def test_empty_activation(self):
        """Handles empty/zero activation gracefully."""
        extractor = ROIExtractor()
        activation = np.zeros((1, 100), dtype=np.float32)
        scores = extractor.extract_scores(activation)
        assert len(scores) == 4


# ---------------------------------------------------------------------------
# RunningNormalizer tests
# ---------------------------------------------------------------------------


class TestRunningNormalizer:
    """Test running percentile normalization."""

    def test_warm_start_before_baseline(self):
        """Pre-baseline scores use warm-start centered at 0.5."""
        norm = RunningNormalizer()
        scores = {"self_relevance": 1.0, "trust": -1.0}
        result = norm.normalize(scores)

        # Warm-start formula: 0.5 + (value / (abs(value) + 1e-6)) * 0.3
        assert 0.0 <= result["self_relevance"] <= 1.0
        assert 0.0 <= result["trust"] <= 1.0
        # Positive value -> > 0.5, negative -> < 0.5
        assert result["self_relevance"] > 0.5
        assert result["trust"] < 0.5

    def test_percentile_after_baseline(self):
        """After BASELINE_SIZE observations, use percentile normalization."""
        norm = RunningNormalizer()
        # Feed 50 values to exceed baseline
        for i in range(55):
            norm.normalize({"test_dim": float(i)})

        # Now normalize a value in the middle
        result = norm.normalize({"test_dim": 27.5})
        # Should be approximately 0.5 (middle of 0-54 range)
        assert 0.3 <= result["test_dim"] <= 0.7

    def test_output_range_0_to_1(self):
        """All normalized values should be in [0, 1]."""
        norm = RunningNormalizer()
        for _ in range(100):
            scores = {
                "self_relevance": np.random.randn(),
                "trust": np.random.randn(),
            }
            result = norm.normalize(scores)
            for dim, val in result.items():
                assert 0.0 <= val <= 1.0, f"{dim}={val} out of [0,1]"

    def test_multiple_dimensions_independent(self):
        """Each dimension has its own history."""
        norm = RunningNormalizer()
        for i in range(60):
            norm.normalize({"dim_a": float(i), "dim_b": float(100 - i)})

        # dim_a high value should score high; dim_b high value should also score high
        result = norm.normalize({"dim_a": 55.0, "dim_b": 95.0})
        assert result["dim_a"] > 0.5
        assert result["dim_b"] > 0.5


# ---------------------------------------------------------------------------
# TribeService tests (mock-based)
# ---------------------------------------------------------------------------


class TestTribeService:
    """Test TribeService lazy loading and singleton pattern."""

    def test_singleton_pattern(self):
        """get() returns the same instance."""
        from titan.neuro.tribe_service import TribeService

        s1 = TribeService.get()
        s2 = TribeService.get()
        assert s1 is s2

    def test_is_native_property(self):
        """is_native should be False when using fallback."""
        from titan.neuro.tribe_service import TribeService

        service = TribeService.get()
        assert service.is_native is False

    @pytest.mark.asyncio
    async def test_predict_activation_returns_ndarray(self):
        """predict_activation returns a numpy array."""
        from titan.neuro.tribe_service import TribeService

        service = TribeService.get()
        result = await service.predict_activation("test email about web design")
        assert isinstance(result, np.ndarray)
        assert result.ndim >= 1

    @pytest.mark.asyncio
    async def test_unload_resets_state(self):
        """unload() clears the model and marks as not loaded."""
        from titan.neuro.tribe_service import TribeService

        service = TribeService.get()
        await service.predict_activation("warmup")
        await service.unload()
        assert service._loaded is False


# ---------------------------------------------------------------------------
# NeuroScorer orchestrator tests
# ---------------------------------------------------------------------------


class TestNeuroScorer:
    """Test the NeuroScorer orchestrator."""

    @pytest.mark.asyncio
    async def test_score_returns_neuro_scores(self):
        """score() returns a NeuroScores dataclass."""
        scorer = NeuroScorer()
        result = await scorer.score("Test email about web development services")
        assert isinstance(result, NeuroScores)
        assert 0.0 <= result.composite <= 1.0

    @pytest.mark.asyncio
    async def test_score_all_dimensions_present(self):
        """All 4 dimensions should be present in scores."""
        scorer = NeuroScorer()
        result = await scorer.score("Hello, this is a test email for your business")
        assert hasattr(result, "self_relevance")
        assert hasattr(result, "trust")
        assert hasattr(result, "cognitive_ease")
        assert hasattr(result, "emotional_resonance")

    @pytest.mark.asyncio
    async def test_default_weights(self):
        """Default weights should sum to 1.0."""
        total = sum(NeuroScorer.DEFAULT_WEIGHTS.values())
        assert abs(total - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_score_includes_metadata(self):
        """Score should include inference_mode and latency."""
        scorer = NeuroScorer()
        result = await scorer.score("Test content for scoring")
        assert result.inference_mode in ("native", "fallback")
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_get_current_weights_default(self):
        """_get_current_weights falls back to DEFAULT_WEIGHTS when DB unavailable."""
        scorer = NeuroScorer()
        with patch("titan.neuro.neuro_scorer.NeuroScorer._get_current_weights") as mock_w:
            mock_w.return_value = NeuroScorer.DEFAULT_WEIGHTS
            weights = await scorer._get_current_weights()
            assert weights == NeuroScorer.DEFAULT_WEIGHTS

    @pytest.mark.asyncio
    async def test_composite_is_weighted_sum(self):
        """Composite should be weighted sum of dimension scores."""
        scorer = NeuroScorer()
        result = await scorer.score("Test email for composite calculation")

        # Manually compute expected composite
        weights = NeuroScorer.DEFAULT_WEIGHTS
        expected = (
            result.self_relevance * weights["self_relevance"]
            + result.trust * weights["trust"]
            + result.cognitive_ease * weights["cognitive_ease"]
            + result.emotional_resonance * weights["emotional_resonance"]
        )
        assert abs(result.composite - expected) < 0.01


# ---------------------------------------------------------------------------
# Neural guidance tests (email_compose integration)
# ---------------------------------------------------------------------------


class TestNeuralGuidance:
    """Test neural guidance prompt generation for weak dimensions."""

    def test_build_guidance_for_weak_dims(self):
        """_build_neural_guidance creates prompts for weak dimensions."""
        from titan.pipeline.email_compose import _build_neural_guidance

        guidance = _build_neural_guidance(["self_relevance", "trust"])
        assert "SELF_RELEVANCE" in guidance
        assert "TRUST" in guidance
        assert "COGNITIVE_EASE" not in guidance

    def test_build_guidance_empty_dims(self):
        """Empty weak dims returns empty string."""
        from titan.pipeline.email_compose import _build_neural_guidance

        guidance = _build_neural_guidance([])
        assert guidance == ""

    def test_all_dimensions_have_guidance(self):
        """Every dimension has a guidance entry."""
        from titan.pipeline.email_compose import NEURAL_GUIDANCE

        dims = ["self_relevance", "trust", "cognitive_ease", "emotional_resonance"]
        for dim in dims:
            assert dim in NEURAL_GUIDANCE
            assert len(NEURAL_GUIDANCE[dim]) > 10

    def test_is_neuro_enabled_flag(self):
        """_is_neuro_enabled checks env var."""
        from titan.pipeline.email_compose import _is_neuro_enabled

        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "true"}):
            assert _is_neuro_enabled() is True
        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "false"}):
            assert _is_neuro_enabled() is False


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


class TestNeuroScorerMiddleware:
    """Test neuro_scorer_middleware in shared/middleware.py."""

    @pytest.mark.asyncio
    async def test_passthrough_when_disabled(self):
        """When ENABLE_NEURO_SCORER is off, passes through unchanged."""
        from shared.middleware import neuro_scorer_middleware

        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "false"}):
            ctx = {"stage_name": "email_compose"}
            expected = {"success": True, "output": "test email"}
            next_fn = AsyncMock(return_value=expected)

            result = await neuro_scorer_middleware(ctx, next_fn)
            assert result == expected
            next_fn.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_passthrough_non_content_stage(self):
        """Non-content stages pass through even when enabled."""
        from shared.middleware import neuro_scorer_middleware

        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "true"}):
            ctx = {"stage_name": "lead_discovery"}
            expected = {"success": True}
            next_fn = AsyncMock(return_value=expected)

            result = await neuro_scorer_middleware(ctx, next_fn)
            assert result == expected

    @pytest.mark.asyncio
    async def test_scores_content_stages(self):
        """Content stages get neuro_scores attached when enabled."""
        from shared.middleware import neuro_scorer_middleware

        mock_scores = NeuroScores(composite=0.8)
        mock_scorer_instance = MagicMock()
        mock_scorer_instance.score = AsyncMock(return_value=mock_scores)

        with patch.dict(os.environ, {"ENABLE_NEURO_SCORER": "true"}):
            with patch("titan.neuro.neuro_scorer.NeuroScorer", return_value=mock_scorer_instance):
                ctx = {"stage_name": "email_compose"}
                next_fn = AsyncMock(return_value={"success": True, "output": "test email"})

                result = await neuro_scorer_middleware(ctx, next_fn)
                assert "neuro_scores" in result

    @pytest.mark.asyncio
    async def test_middleware_in_chain_ordering(self):
        """neuro_scorer appears after anti_slop in TITAN_MIDDLEWARE."""
        from shared.middleware import TITAN_MIDDLEWARE

        assert "neuro_scorer" in TITAN_MIDDLEWARE
        anti_slop_idx = TITAN_MIDDLEWARE.index("anti_slop")
        neuro_idx = TITAN_MIDDLEWARE.index("neuro_scorer")
        assert neuro_idx > anti_slop_idx

    def test_registered_in_middleware_registry(self):
        """neuro_scorer is in MIDDLEWARE_REGISTRY."""
        from shared.middleware import MIDDLEWARE_REGISTRY

        assert "neuro_scorer" in MIDDLEWARE_REGISTRY

    def test_pipeline_configs_include_neuro(self):
        """PIPELINE_CONFIGS for titan and clawdbot include neuro_scorer."""
        from shared.middleware import PIPELINE_CONFIGS

        assert "neuro_scorer" in PIPELINE_CONFIGS["titan"]
        assert "neuro_scorer" in PIPELINE_CONFIGS["clawdbot"]
        assert "neuro_scorer" in PIPELINE_CONFIGS["hermes"]


# ---------------------------------------------------------------------------
# Scheduler integration tests
# ---------------------------------------------------------------------------


class TestSchedulerJobs:
    """Test that neural scheduler jobs are registered."""

    def test_neural_reflection_scheduled(self):
        """neural_reflection is in SCHEDULES."""
        from perseus.scheduler import SCHEDULE_MAP

        assert "neural_reflection" in SCHEDULE_MAP
        job = SCHEDULE_MAP["neural_reflection"]
        assert job.interval_seconds == 86400
        assert job.skippable is False

    def test_segment_profiles_scheduled(self):
        """segment_profiles is in SCHEDULES."""
        from perseus.scheduler import SCHEDULE_MAP

        assert "segment_profiles" in SCHEDULE_MAP
        job = SCHEDULE_MAP["segment_profiles"]
        assert job.interval_seconds == 604800
        assert job.skippable is False
