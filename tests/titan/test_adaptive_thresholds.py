"""Tests for titan/adaptive_thresholds.py — Thompson sampling bandits.

Covers:
- BetaBandit convergence, sampling, and update mechanics
- AdaptiveThresholds ThresholdProvider Protocol compliance
- Warm-start priors (Beta(10,2))
- ExperimentManager creation and assignment
- meta_evaluations logging
- Feature flag gating
- Zero HyperAgents code verification
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from shared.contracts import ThresholdProvider
from titan.adaptive_thresholds import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    THRESHOLD_NAMES,
    AdaptiveThresholds,
    BetaBandit,
    ExperimentManager,
    collect_training_signal,
    is_enabled,
    run_daily_training,
)

# ---------------------------------------------------------------------------
# BetaBandit tests
# ---------------------------------------------------------------------------


class TestBetaBandit:
    """Test the core Thompson sampling bandit."""

    def test_sample_in_range(self):
        """Samples from Beta(10, 2) should be in [0, 1]."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        for _ in range(100):
            s = b.sample()
            assert 0.0 <= s <= 1.0, f"Sample {s} out of range"

    def test_mean_formula(self):
        """Mean = alpha / (alpha + beta)."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        assert abs(b.mean - 10.0 / 12.0) < 1e-10

    def test_update_success(self):
        """Success (reward=1.0) increments alpha."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        b.update(1.0)
        assert b.alpha == 11.0
        assert b.beta == 2.0

    def test_update_failure(self):
        """Failure (reward=0.0) increments beta."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        b.update(0.0)
        assert b.alpha == 10.0
        assert b.beta == 3.0

    def test_convergence_after_100_observations(self):
        """After 100 mostly-successful outcomes, mean should shift upward."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        # 80 successes, 20 failures
        for _ in range(80):
            b.update(1.0)
        for _ in range(20):
            b.update(0.0)
        # Mean should be around (10+80)/(10+80+2+20) = 90/112 ~ 0.804
        assert abs(b.mean - 90.0 / 112.0) < 1e-10
        # After many mostly-success observations, mean is meaningful
        assert b.alpha == 90.0
        assert b.beta == 22.0

    def test_convergence_toward_low_conversion(self):
        """After 100 mostly-failure outcomes, mean should shift downward."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        for _ in range(20):
            b.update(1.0)
        for _ in range(80):
            b.update(0.0)
        # Mean = (10+20)/(10+20+2+80) = 30/112 ~ 0.268
        assert b.mean < 0.5

    def test_warm_start_prior_dominates_initially(self):
        """With Beta(10,2), a few observations don't change much."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        b.update(0.0)
        b.update(0.0)
        # Mean only drops from 0.833 to 10/14 = 0.714
        assert b.mean > 0.5, "Prior should dominate with few observations"

    def test_confidence_interval_returns_none_without_scipy(self):
        """CI returns None if scipy is not available."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        # scipy may or may not be available; test either case
        ci = b.confidence_interval
        if ci is not None:
            lo, hi = ci
            assert 0.0 <= lo < hi <= 1.0
        # If None, that's also acceptable (scipy not installed)

    def test_total_updates_property(self):
        """total_updates tracks approximate update count."""
        b = BetaBandit("test", alpha=10.0, beta=2.0)
        assert b.total_updates == 0
        b.update(1.0)
        assert b.total_updates == 1
        b.update(0.0)
        assert b.total_updates == 2


# ---------------------------------------------------------------------------
# AdaptiveThresholds tests
# ---------------------------------------------------------------------------


class TestAdaptiveThresholds:
    """Test the threshold manager."""

    def test_implements_threshold_provider(self):
        """Must satisfy ThresholdProvider Protocol."""
        at = AdaptiveThresholds()
        assert isinstance(at, ThresholdProvider)

    def test_get_threshold_default_fallback(self):
        """Unknown threshold returns default hardcoded value."""
        at = AdaptiveThresholds()
        val = at.get_threshold("reply_rate_threshold")
        assert val == 1.5  # default fallback

    def test_get_threshold_from_loaded_bandit(self):
        """After loading, get_threshold samples from bandit and scales."""
        at = AdaptiveThresholds()
        at._bandits["reply_rate_threshold"] = BetaBandit("reply_rate_threshold", alpha=100.0, beta=1.0)
        val = at.get_threshold("reply_rate_threshold")
        # Scale factor for reply_rate_threshold is 5.0
        assert 0.0 <= val <= 5.0

    def test_update_creates_bandit_if_missing(self):
        """update() with unknown name creates new bandit."""
        at = AdaptiveThresholds()
        at.update("new_threshold", 1.0)
        assert "new_threshold" in at._bandits
        assert at._bandits["new_threshold"].alpha == DEFAULT_ALPHA + 1.0

    def test_update_modifies_existing_bandit(self):
        """update() adjusts existing bandit posterior."""
        at = AdaptiveThresholds()
        at._bandits["test"] = BetaBandit("test", alpha=10.0, beta=2.0)
        at.update("test", 1.0)
        assert at._bandits["test"].alpha == 11.0

    def test_loaded_names(self):
        """loaded_names returns all cached bandit names."""
        at = AdaptiveThresholds()
        at._bandits["a"] = BetaBandit("a")
        at._bandits["b"] = BetaBandit("b")
        assert sorted(at.loaded_names()) == ["a", "b"]

    def test_get_bandit_returns_none_for_missing(self):
        at = AdaptiveThresholds()
        assert at.get_bandit("nonexistent") is None

    def test_default_for_all_thresholds(self):
        """All 5 threshold names have defaults."""
        at = AdaptiveThresholds()
        defaults = {
            "reply_rate_threshold": 1.5,
            "interest_rate_threshold": 12.0,
            "proposal_backlog_threshold": 3.0,
            "uninvoiced_threshold": 2.0,
            "missing_email_threshold": 10.0,
        }
        for name, expected in defaults.items():
            assert at._default_for(name) == expected


# ---------------------------------------------------------------------------
# Async DB tests (mocked)
# ---------------------------------------------------------------------------


class TestAdaptiveThresholdsAsync:
    """Test async DB methods with mocked database."""

    @pytest.mark.asyncio
    async def test_load_from_db(self):
        """load_from_db populates bandit from DB row."""
        at = AdaptiveThresholds()
        mock_row = {"alpha": "15.0", "beta": "5.0"}
        with patch("titan.adaptive_thresholds.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_row
            await at.load_from_db("reply_rate_threshold")
        b = at.get_bandit("reply_rate_threshold")
        assert b is not None
        assert b.alpha == 15.0
        assert b.beta == 5.0

    @pytest.mark.asyncio
    async def test_load_from_db_missing(self):
        """load_from_db creates default bandit when no DB row."""
        at = AdaptiveThresholds()
        with patch("titan.adaptive_thresholds.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            await at.load_from_db("reply_rate_threshold")
        b = at.get_bandit("reply_rate_threshold")
        assert b is not None
        assert b.alpha == DEFAULT_ALPHA
        assert b.beta == DEFAULT_BETA

    @pytest.mark.asyncio
    async def test_save_to_db(self):
        """save_to_db persists bandit state."""
        at = AdaptiveThresholds()
        at._bandits["test"] = BetaBandit("test", alpha=15.0, beta=5.0)
        with patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock) as mock_exec:
            await at.save_to_db("test")
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "UPDATE adaptive_thresholds" in args[0]
            assert args[1][0] == 15.0  # alpha
            assert args[1][1] == 5.0   # beta

    @pytest.mark.asyncio
    async def test_log_meta_evaluation(self):
        """log_meta_evaluation inserts to meta_evaluations."""
        at = AdaptiveThresholds()
        at._bandits["test"] = BetaBandit("test", alpha=10.0, beta=2.0)
        with patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock) as mock_exec:
            await at.log_meta_evaluation(
                name="test",
                old_value=0.8,
                new_value=0.85,
                reason="bandit_update",
            )
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "INSERT INTO meta_evaluations" in args[0]

    @pytest.mark.asyncio
    async def test_update_and_persist(self):
        """update_and_persist does full cycle: update + save + log."""
        at = AdaptiveThresholds()
        at._bandits["test"] = BetaBandit("test", alpha=10.0, beta=2.0)
        with patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock) as mock_exec:
            await at.update_and_persist("test", 1.0)
            # Should call execute twice: save_to_db + log_meta_evaluation
            assert mock_exec.call_count == 2
        assert at._bandits["test"].alpha == 11.0

    @pytest.mark.asyncio
    async def test_get_threshold_async(self):
        """get_threshold_async loads from DB then samples."""
        at = AdaptiveThresholds()
        mock_row = {"alpha": "50.0", "beta": "50.0"}
        with patch("titan.adaptive_thresholds.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_row
            val = await at.get_threshold_async("test")
            assert 0.0 <= val <= 1.0

    @pytest.mark.asyncio
    async def test_load_all_from_db(self):
        """load_all_from_db loads all thresholds."""
        at = AdaptiveThresholds()
        mock_rows = [
            {"threshold_name": "reply_rate_threshold", "alpha": "10", "beta": "2"},
            {"threshold_name": "interest_rate_threshold", "alpha": "12", "beta": "3"},
        ]
        with patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_rows
            await at.load_all_from_db()
        assert len(at.loaded_names()) == 2


# ---------------------------------------------------------------------------
# Training signal tests
# ---------------------------------------------------------------------------


class TestTrainingSignal:
    """Test pipeline outcome collection."""

    @pytest.mark.asyncio
    async def test_collect_training_signal_conversions(self):
        """Converted clients produce outcome=1.0 signals."""
        mock_rows = [
            {"status": "paid", "updated_at": "2026-03-28"},
            {"status": "lost", "updated_at": "2026-03-28"},
        ]
        with patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_rows
            signals = await collect_training_signal()

        # 2 rows x 5 thresholds = 10 signals
        assert len(signals) == 10
        # First row (paid) -> 1.0, second (lost) -> 0.0
        outcomes = [s[1] for s in signals]
        assert outcomes.count(1.0) == 5  # 5 thresholds x 1 conversion
        assert outcomes.count(0.0) == 5  # 5 thresholds x 1 non-conversion

    @pytest.mark.asyncio
    async def test_collect_training_signal_empty(self):
        """No recent outcomes produces empty signal list."""
        with patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            signals = await collect_training_signal()
        assert signals == []

    @pytest.mark.asyncio
    async def test_run_daily_training_disabled(self):
        """Daily training is skipped when feature flag is off."""
        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "false"}):
            result = await run_daily_training()
        assert result == 0

    @pytest.mark.asyncio
    async def test_run_daily_training_enabled(self):
        """Daily training processes signals when enabled."""
        mock_rows = [{"status": "paid", "updated_at": "2026-03-28"}]
        with (
            patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "true"}),
            patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch_all,
            patch("titan.adaptive_thresholds.fetch_one", new_callable=AsyncMock) as mock_fetch_one,
            patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock),
            patch("titan.adaptive_thresholds.emit_event", new_callable=AsyncMock),
        ):
            mock_fetch_all.return_value = mock_rows
            # load_all_from_db returns rows for all thresholds
            mock_fetch_one.return_value = {"alpha": "10", "beta": "2"}
            # Override fetch_all to return different things for different queries
            call_count = [0]

            async def fetch_all_side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # collect_training_signal (called first in run_daily_training)
                    return mock_rows
                # load_all_from_db (called second)
                return [
                    {"threshold_name": n, "alpha": "10", "beta": "2"}
                    for n in THRESHOLD_NAMES
                ]

            mock_fetch_all.side_effect = fetch_all_side_effect
            result = await run_daily_training()
        assert result == 5  # 1 client x 5 thresholds


# ---------------------------------------------------------------------------
# ExperimentManager tests
# ---------------------------------------------------------------------------


class TestExperimentManager:
    """Test A/B shadow experiments."""

    @pytest.mark.asyncio
    async def test_create_experiment(self):
        """create_experiment inserts into meta_evaluations and emits event."""
        mgr = ExperimentManager()
        with (
            patch("titan.adaptive_thresholds.fetch_one", new_callable=AsyncMock) as mock_fetch,
            patch("titan.adaptive_thresholds.emit_event", new_callable=AsyncMock) as mock_event,
        ):
            mock_fetch.return_value = {"id": 42}
            exp_id = await mgr.create_experiment(
                name="test_exp",
                threshold_name="reply_rate_threshold",
                variant_alpha=20.0,
                variant_beta=5.0,
            )
        assert exp_id == 42
        mock_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_assign_lead_50_50(self):
        """Lead assignment produces control/variant at ~50/50."""
        mgr = ExperimentManager()
        mock_experiments = [
            {
                "id": 1,
                "threshold_name": "reply_rate_threshold",
                "old_value": 20.0,
                "new_value": 5.0,
                "change_reason": "experiment_created:test_exp",
                "sample_size": 0,
            }
        ]
        with patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_experiments
            # Run 100 assignments to check distribution
            control_count = 0
            variant_count = 0
            for _ in range(100):
                assignments = await mgr.assign_lead(f"lead_{_}")
                if assignments.get("test_exp") == "control":
                    control_count += 1
                else:
                    variant_count += 1
        # Should be roughly 50/50 (within statistical bounds)
        assert 20 <= control_count <= 80, f"control={control_count}, variant={variant_count}"
        assert 20 <= variant_count <= 80

    @pytest.mark.asyncio
    async def test_record_outcome(self):
        """record_outcome stores data in meta_evaluations."""
        mgr = ExperimentManager()
        with patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock) as mock_exec:
            await mgr.record_outcome("test_exp", "control", 1.0)
            mock_exec.assert_called_once()
            args = mock_exec.call_args[0]
            assert "INSERT INTO meta_evaluations" in args[0]

    @pytest.mark.asyncio
    async def test_evaluate_insufficient_data(self):
        """Experiments without enough data return insufficient_data status."""
        mgr = ExperimentManager()
        with patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch:
            call_count = [0]

            async def side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [{"change_reason": "experiment_created:test_exp"}]
                return []  # no outcomes

            mock_fetch.side_effect = side_effect
            results = await mgr.evaluate_experiments()
        assert len(results) == 1
        assert results[0]["status"] == "insufficient_data"

    @pytest.mark.asyncio
    async def test_evaluate_with_sufficient_data(self):
        """Experiments with enough data return concluded status with winner."""
        mgr = ExperimentManager()

        # Build mock data: 30 control outcomes, 30 variant outcomes
        control_rows = [{"outcome": 1.0}] * 20 + [{"outcome": 0.0}] * 10
        variant_rows = [{"outcome": 1.0}] * 25 + [{"outcome": 0.0}] * 5

        with (
            patch("titan.adaptive_thresholds.fetch_all", new_callable=AsyncMock) as mock_fetch,
            patch("titan.adaptive_thresholds.execute", new_callable=AsyncMock),
        ):
            call_count = [0]

            async def side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [{"change_reason": "experiment_created:test_exp"}]
                elif call_count[0] == 2:
                    return control_rows
                elif call_count[0] == 3:
                    return variant_rows
                return []

            mock_fetch.side_effect = side_effect
            results = await mgr.evaluate_experiments()

        assert len(results) == 1
        assert results[0]["status"] == "concluded"
        assert results[0]["winner"] == "variant"
        assert results[0]["mean_variant"] > results[0]["mean_control"]


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """Test feature flag gating."""

    def test_is_enabled_false_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ENABLE_BANDIT_EXPANSION", None)
            assert is_enabled() is False

    def test_is_enabled_true(self):
        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "true"}):
            assert is_enabled() is True

    def test_is_enabled_one(self):
        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "1"}):
            assert is_enabled() is True

    def test_is_enabled_false(self):
        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "false"}):
            assert is_enabled() is False


# ---------------------------------------------------------------------------
# License compliance
# ---------------------------------------------------------------------------


class TestLicenseCompliance:
    """Verify zero HyperAgents code in production paths."""

    def test_no_hyperagents_imports_in_adaptive_thresholds(self):
        """The adaptive thresholds module must not import or use HyperAgents."""
        import titan.adaptive_thresholds as mod
        source = open(mod.__file__).read()
        # No imports from hyperagents packages
        assert "from hyperagent" not in source.lower(), "HyperAgents import found!"
        assert "import hyperagent" not in source.lower(), "HyperAgents import found!"
        assert "arXiv:2603.19461" not in source, "HyperAgents paper ref found!"

    def test_no_hyperagents_imports_in_contracts(self):
        """Contracts module must not import HyperAgents."""
        import shared.contracts as mod
        source = open(mod.__file__).read()
        assert "from hyperagent" not in source.lower()
        assert "import hyperagent" not in source.lower()


# ---------------------------------------------------------------------------
# Threshold names
# ---------------------------------------------------------------------------


class TestThresholdNames:
    """Verify threshold configuration."""

    def test_five_thresholds_defined(self):
        assert len(THRESHOLD_NAMES) == 5

    def test_threshold_names_match_expansion_engine(self):
        """Names should cover the 5 hardcoded thresholds in expansion.py."""
        expected = {
            "reply_rate_threshold",
            "interest_rate_threshold",
            "proposal_backlog_threshold",
            "uninvoiced_threshold",
            "missing_email_threshold",
        }
        assert set(THRESHOLD_NAMES) == expected


# ---------------------------------------------------------------------------
# Expansion.py integration tests (feature flag)
# ---------------------------------------------------------------------------


class TestExpansionIntegration:
    """Test that expansion.py wiring respects feature flag."""

    def test_flag_off_uses_hardcoded_thresholds(self):
        """ENABLE_BANDIT_EXPANSION=off -> original hardcoded values."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_BANDIT_EXPANSION", None)
            from titan.expansion import _detect_revenue_bottlenecks

            metrics = {"emails_sent_14d": 200, "reply_rate_14d": 1.0}
            bottlenecks = _detect_revenue_bottlenecks(metrics)
            # Hardcoded threshold is 1.5; 1.0 < 1.5 -> bottleneck detected
            assert any(b["bottleneck"] == "low_reply_rate" for b in bottlenecks)

    def test_flag_on_uses_adaptive_thresholds(self):
        """ENABLE_BANDIT_EXPANSION=true -> adaptive bandits used."""
        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "true"}):
            from titan.expansion import _detect_revenue_bottlenecks

            # With adaptive thresholds, results depend on bandit sampling
            metrics = {"emails_sent_14d": 200, "reply_rate_14d": 1.0}
            bottlenecks = _detect_revenue_bottlenecks(metrics)
            assert isinstance(bottlenecks, list)

    def test_flag_off_empty_metrics_no_bottlenecks(self):
        """No metrics -> no bottlenecks regardless of flag."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_BANDIT_EXPANSION", None)
            from titan.expansion import _detect_revenue_bottlenecks

            assert _detect_revenue_bottlenecks({}) == []

    def test_instant_rollback(self):
        """Feature flag toggles cleanly without code changes."""
        from titan.expansion import _detect_revenue_bottlenecks, _is_bandit_expansion_enabled

        metrics = {"emails_sent_14d": 200, "reply_rate_14d": 1.0}

        with patch.dict(os.environ, {"ENABLE_BANDIT_EXPANSION": "true"}):
            assert _is_bandit_expansion_enabled() is True
            _detect_revenue_bottlenecks(metrics)  # should not crash

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_BANDIT_EXPANSION", None)
            assert _is_bandit_expansion_enabled() is False
            _detect_revenue_bottlenecks(metrics)  # should not crash


# ---------------------------------------------------------------------------
# License audit script tests
# ---------------------------------------------------------------------------


class TestLicenseAuditScript:
    """Verify license-audit.sh passes."""

    def test_license_audit_script_passes(self):
        """scripts/license-audit.sh exits 0."""
        import subprocess

        result = subprocess.run(
            ["bash", "scripts/license-audit.sh"],
            capture_output=True,
            text=True,
            cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        )
        assert result.returncode == 0, (
            f"License audit failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "CLEAN" in result.stdout


# ---------------------------------------------------------------------------
# Bandit convergence with stochastic outcomes
# ---------------------------------------------------------------------------


class TestBanditConvergenceStochastic:
    """Test convergence with random outcomes (complementing deterministic tests)."""

    def test_bandit_converges_100_random_outcomes(self):
        """With 80% success rate, bandit mean converges near 0.8 after 100 random outcomes."""
        import numpy as np

        np.random.seed(42)
        b = BetaBandit("test", alpha=1.0, beta=1.0)  # uninformative prior

        for _ in range(100):
            outcome = 1.0 if np.random.random() < 0.8 else 0.0
            b.update(outcome)

        # Mean should be near 0.8 (within tolerance)
        assert abs(b.mean - 0.8) < 0.1, f"Mean {b.mean} not near 0.8"

    def test_warm_start_washes_out(self):
        """Beta(10,2) prior washes out after ~50 observations with 50% rate."""
        import numpy as np

        np.random.seed(99)
        b = BetaBandit("test", alpha=DEFAULT_ALPHA, beta=DEFAULT_BETA)

        for _ in range(50):
            outcome = 1.0 if np.random.random() < 0.5 else 0.0
            b.update(outcome)

        # Mean should have moved away from prior (0.833) toward 0.5
        assert b.mean < 0.75, f"Prior still dominates: mean={b.mean}"
