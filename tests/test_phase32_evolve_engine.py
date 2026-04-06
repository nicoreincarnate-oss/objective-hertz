"""Phase 32: AlphaEvolve Self-Improvement Engine — comprehensive tests.

Tests cover:
1. EvolveCandidate / EvolveConfig data models
2. Full evolution loop (end-to-end with mock evaluator)
3. Safety boundaries: risk gate, protected paths, mutation size
4. Daily run cap enforcement
5. Rollback on regression
6. Promotion on success
7. Bandit integration
8. Built-in evaluators (prompt, email, rubric, pipeline_param)
9. Cost cap enforcement
10. Convergence detection
11. Feature flag gating
12. Artifact wiring in learning_loop
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# Ensure feature flag is on for tests
os.environ["ALPHA_EVOLVE"] = "1"

from shared.evolve_engine import (
    COST_CAP_PER_RUN,
    MAX_DAILY_RUNS,
    MAX_MUTATION_TOKENS,
    PROTECTED_PATHS,
    RISK_THRESHOLD,
    VALID_ARTIFACT_TYPES,
    EvolveCandidate,
    EvolveConfig,
    EvolveEngine,
    EvolveRollback,
    _DailyRunCounter,
    _score_risk,
    _token_delta,
    evaluate_email_template,
    evaluate_pipeline_param,
    evaluate_prompt,
    evaluate_scoring_rubric,
    get_evaluator,
)

# ---------------------------------------------------------------------------
# 1. Data model tests
# ---------------------------------------------------------------------------


class TestDataModels:
    def test_evolve_candidate_defaults(self):
        c = EvolveCandidate(
            id="test_001",
            experiment_id="exp_1",
            artifact_type="prompt",
            content="Test prompt",
        )
        assert c.id == "test_001"
        assert c.parent_id is None
        assert c.generation == 0
        assert c.island == 0
        assert c.metrics == {}
        assert c.status == "active"
        assert c.created_at > 0

    def test_evolve_config_defaults(self):
        cfg = EvolveConfig()
        assert cfg.population_size == 20
        assert cfg.num_islands == 2
        assert cfg.exploitation_ratio == 0.7
        assert cfg.max_generations == 10
        assert cfg.breadth_model == "fast"
        assert cfg.depth_model == "smart"
        assert cfg.min_trials == 20
        assert cfg.min_hours == 48.0
        assert cfg.regression_threshold == 0.05

    def test_valid_artifact_types(self):
        assert "prompt" in VALID_ARTIFACT_TYPES
        assert "email_template" in VALID_ARTIFACT_TYPES
        assert "scoring_rubric" in VALID_ARTIFACT_TYPES
        assert "pipeline_param" in VALID_ARTIFACT_TYPES
        assert "code" not in VALID_ARTIFACT_TYPES


# ---------------------------------------------------------------------------
# 2. Full evolution loop
# ---------------------------------------------------------------------------


class TestEvolutionLoop:
    @pytest.mark.asyncio
    async def test_full_evolution_with_mock_evaluator(self):
        """End-to-end: seed -> evolve -> best candidate returned."""
        call_count = 0

        async def mock_llm(prompt, system, model, max_tokens):
            nonlocal call_count
            call_count += 1
            # Return a slightly modified version of the seed
            return "You must always respond politely and clearly."

        async def mock_evaluator(content: str) -> dict[str, float]:
            # Deterministic scores based on content length
            return {
                "clarity": min(1.0, len(content) / 100.0),
                "specificity": 0.8,
                "efficiency": 0.7,
            }

        config = EvolveConfig(
            population_size=6,
            num_islands=2,
            max_generations=2,
        )

        engine = EvolveEngine(llm_generate=mock_llm)
        best = await engine.evolve(
            seed="You must always respond politely.",
            artifact_type="prompt",
            evaluator=mock_evaluator,
            config=config,
            experiment_id="test_full_evo",
        )

        assert best is not None
        assert best.experiment_id == "test_full_evo"
        assert best.artifact_type == "prompt"
        assert best.metrics  # should have been evaluated
        assert sum(best.metrics.values()) > 0

    @pytest.mark.asyncio
    async def test_evolution_populates_islands(self):
        """Candidates should be distributed across islands."""
        async def mock_llm(prompt, system, model, max_tokens):
            return "Improved version of the prompt."

        async def mock_evaluator(content: str) -> dict[str, float]:
            return {"quality": 0.8}

        config = EvolveConfig(
            population_size=10,
            num_islands=2,
            max_generations=1,
        )

        engine = EvolveEngine(llm_generate=mock_llm)
        # Just test population initialization
        pop = engine._init_population("seed text", "prompt", "exp_islands", config)
        islands = {c.island for c in pop}
        assert 0 in islands
        assert 1 in islands

    @pytest.mark.asyncio
    async def test_invalid_artifact_type_raises(self):
        engine = EvolveEngine(llm_generate=AsyncMock(return_value="x"))
        with pytest.raises(ValueError, match="Invalid artifact_type"):
            await engine.evolve(
                seed="test",
                artifact_type="code",
                evaluator=AsyncMock(return_value={"q": 1.0}),
            )


# ---------------------------------------------------------------------------
# 3. Safety boundaries
# ---------------------------------------------------------------------------


class TestSafetyBoundaries:
    @pytest.mark.asyncio
    async def test_protected_path_risk_score(self):
        """Mutations referencing protected paths get high risk scores."""
        risk = await _score_risk("prompt", "modify wallet.py to increase budget")
        assert risk >= 0.9

    @pytest.mark.asyncio
    async def test_safe_content_low_risk(self):
        """Normal prompt content gets low risk."""
        risk = await _score_risk("prompt", "Improve email greeting clarity")
        assert risk < RISK_THRESHOLD

    @pytest.mark.asyncio
    async def test_security_path_blocked(self):
        risk = await _score_risk("prompt", "changes to security/ module")
        assert risk >= 0.9

    def test_mutation_size_enforcement(self):
        """Token delta > 50 should be detected."""
        original = "Hello world"
        # A drastically different mutation with many unique words
        mutated = " ".join([f"word{i}" for i in range(100)])
        delta = _token_delta(original, mutated)
        assert delta > MAX_MUTATION_TOKENS

    def test_small_mutation_passes(self):
        """Small changes should have small token delta."""
        original = "You must always respond politely."
        mutated = "You must always respond politely and clearly."
        delta = _token_delta(original, mutated)
        assert delta <= MAX_MUTATION_TOKENS


# ---------------------------------------------------------------------------
# 4. Daily run cap
# ---------------------------------------------------------------------------


class TestDailyRunCap:
    def test_daily_counter_allows_runs(self):
        counter = _DailyRunCounter()
        assert counter.can_run()
        assert counter.count_today() == 0

    def test_daily_counter_blocks_after_cap(self):
        counter = _DailyRunCounter()
        for _ in range(MAX_DAILY_RUNS):
            counter.record_run()
        assert not counter.can_run()
        assert counter.count_today() == MAX_DAILY_RUNS

    @pytest.mark.asyncio
    async def test_evolution_rejects_after_daily_cap(self):
        """After MAX_DAILY_RUNS, evolve() should raise ValueError."""
        # Temporarily monkey-patch the global counter
        from shared import evolve_engine

        original_counter = evolve_engine._daily_counter
        try:
            test_counter = _DailyRunCounter()
            for _ in range(MAX_DAILY_RUNS):
                test_counter.record_run()
            evolve_engine._daily_counter = test_counter

            engine = EvolveEngine(llm_generate=AsyncMock(return_value="x"))
            with pytest.raises(ValueError, match="Daily evolution cap"):
                await engine.evolve(
                    seed="test",
                    artifact_type="prompt",
                    evaluator=AsyncMock(return_value={"q": 1.0}),
                )
        finally:
            evolve_engine._daily_counter = original_counter


# ---------------------------------------------------------------------------
# 5. Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    @pytest.mark.asyncio
    async def test_regression_detected(self):
        rb = EvolveRollback()
        result = await rb.check_regression(
            experiment_id="exp_1",
            candidate_id="c_001",
            baseline_metrics={"open_rate": 0.30},
            current_metrics={"open_rate": 0.20},
            trial_count=25,
            hours_elapsed=50.0,
            regression_threshold=0.05,
            min_trials=20,
            min_hours=48.0,
        )
        assert result == "open_rate"

    @pytest.mark.asyncio
    async def test_no_regression_when_improving(self):
        rb = EvolveRollback()
        result = await rb.check_regression(
            experiment_id="exp_1",
            candidate_id="c_001",
            baseline_metrics={"open_rate": 0.30},
            current_metrics={"open_rate": 0.35},
            trial_count=25,
            hours_elapsed=50.0,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_regression_before_min_data(self):
        """Should not judge before min_trials AND min_hours."""
        rb = EvolveRollback()
        result = await rb.check_regression(
            experiment_id="exp_1",
            candidate_id="c_001",
            baseline_metrics={"open_rate": 0.30},
            current_metrics={"open_rate": 0.10},  # severe regression
            trial_count=5,  # too few
            hours_elapsed=2.0,  # too early
            min_trials=20,
            min_hours=48.0,
        )
        assert result is None

    @pytest.mark.asyncio
    @patch("shared.evolve_engine.EvolveRollback._update_candidate_status", new_callable=AsyncMock)
    @patch("shared.evolve_engine.EvolveRollback._log_action", new_callable=AsyncMock)
    async def test_rollback_execution(self, mock_log, mock_status):
        rb = EvolveRollback()
        result = await rb.rollback(
            experiment_id="exp_1",
            candidate_id="c_001",
            metric_name="open_rate",
            baseline_value=0.30,
            current_value=0.20,
            trial_count=25,
            hours_elapsed=50.0,
        )
        assert result is True
        mock_status.assert_called_once_with("c_001", "rolled_back")
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Promotion
# ---------------------------------------------------------------------------


class TestPromotion:
    @pytest.mark.asyncio
    @patch("shared.evolve_engine.EvolveRollback._update_candidate_status", new_callable=AsyncMock)
    @patch("shared.evolve_engine.EvolveRollback._log_action", new_callable=AsyncMock)
    async def test_promote_execution(self, mock_log, mock_status):
        rb = EvolveRollback()
        result = await rb.promote(
            experiment_id="exp_1",
            candidate_id="c_001",
            metrics={"open_rate": 0.35, "ctr": 0.12},
            trial_count=30,
            hours_elapsed=72.0,
        )
        assert result is True
        mock_status.assert_called_once_with("c_001", "promoted")
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Bandit integration
# ---------------------------------------------------------------------------


class TestBanditIntegration:
    @pytest.mark.asyncio
    async def test_register_candidates_as_arms(self):
        """Top candidates should become bandit arms."""
        candidates = [
            EvolveCandidate(
                id=f"c_{i}",
                experiment_id="exp_bandit",
                artifact_type="prompt",
                content=f"Prompt variant {i}",
                metrics={"quality": 0.5 + i * 0.1},
            )
            for i in range(5)
        ]

        engine = EvolveEngine(llm_generate=AsyncMock(return_value="x"))
        await engine.register_with_bandit("exp_bandit", candidates, top_k=3)

        # Verify the bandit now has the arms
        from shared.bandit import get_bandit
        bandit = get_bandit()
        stats = bandit.get_stats("evolve_exp_bandit")
        assert "status" not in stats or stats.get("status") != "not_found"
        assert "arms" in stats
        assert len(stats["arms"]) == 3


# ---------------------------------------------------------------------------
# 8. Built-in evaluators
# ---------------------------------------------------------------------------


class TestBuiltInEvaluators:
    @pytest.mark.asyncio
    async def test_prompt_evaluator(self):
        content = """You must always respond politely.

Ensure every response includes a greeting.

For example, start with "Hello" or "Hi there".

Never skip the verification step.
Check the output for correctness."""

        scores = await evaluate_prompt(content)
        assert "clarity" in scores
        assert "specificity" in scores
        assert "length_efficiency" in scores
        assert all(0.0 <= v <= 1.0 for v in scores.values())
        # Has sections and examples, clarity should be decent
        assert scores["clarity"] >= 0.5

    @pytest.mark.asyncio
    async def test_email_evaluator(self):
        content = """Subject: Special offer for {first_name}

Hi {first_name},

We have a great deal for {{company}}.

Click here to learn more and get started today.

To unsubscribe, click below.
123 Main Street, Suite 100"""

        scores = await evaluate_email_template(content)
        assert "compliance" in scores
        assert "personalization" in scores
        assert "structure" in scores
        assert scores["compliance"] > 0.0  # has unsubscribe + address
        assert scores["personalization"] > 0.0  # has merge fields

    @pytest.mark.asyncio
    async def test_scoring_rubric_evaluator(self):
        content = """Lead Score Rubric:
- Company size score: 1-10 (weight: 0.3)
- Industry relevance rating: 1-10 (weight: 0.25)
- Budget dimension factor: 0.0-1.0 (weight: 0.25)
- Engagement criterion: 1-5 (weight: 0.2)
- Priority: high importance for enterprise leads"""

        scores = await evaluate_scoring_rubric(content)
        assert "coverage" in scores
        assert "measurability" in scores
        assert "balance" in scores
        assert all(0.0 <= v <= 1.0 for v in scores.values())

    @pytest.mark.asyncio
    async def test_pipeline_param_evaluator(self):
        content = """# Pipeline config
batch_size: 10  # max items per batch
timeout: 30  # seconds, default timeout
min_confidence: 0.7  # threshold for acceptance
max_retries: 3  # limit on retries
- Each param has a bound"""

        scores = await evaluate_pipeline_param(content)
        assert "completeness" in scores
        assert "documentation" in scores
        assert "safety" in scores
        assert all(0.0 <= v <= 1.0 for v in scores.values())

    @pytest.mark.asyncio
    async def test_empty_content_evaluators(self):
        """All evaluators handle empty content gracefully."""
        for evaluator in [evaluate_prompt, evaluate_email_template,
                          evaluate_scoring_rubric, evaluate_pipeline_param]:
            scores = await evaluator("")
            assert all(v == 0.0 for v in scores.values())

    def test_get_evaluator(self):
        for atype in VALID_ARTIFACT_TYPES:
            evaluator = get_evaluator(atype)
            assert callable(evaluator)

    def test_get_evaluator_invalid(self):
        with pytest.raises(ValueError, match="No evaluator"):
            get_evaluator("nonexistent_type")


# ---------------------------------------------------------------------------
# 9. Cost cap
# ---------------------------------------------------------------------------


class TestCostCap:
    @pytest.mark.asyncio
    async def test_cost_cap_stops_mutations(self):
        """When run_cost exceeds cap, mutations should stop."""
        call_count = 0

        async def expensive_llm(prompt, system, model, max_tokens):
            nonlocal call_count
            call_count += 1
            return "improved text"

        config = EvolveConfig(
            population_size=4,
            num_islands=1,
            max_generations=3,
        )

        engine = EvolveEngine(llm_generate=expensive_llm)
        # Artificially set high cost to test cap
        engine._run_cost = COST_CAP_PER_RUN + 0.01

        parents = [
            EvolveCandidate(
                id="p_0", experiment_id="exp_cost", artifact_type="prompt",
                content="seed", metrics={"q": 0.5},
            ),
        ]

        children = await engine._generate_mutations(
            parents, "prompt", "exp_cost", 1, config,
        )
        # Should produce zero children because cost cap was already exceeded
        assert len(children) == 0


# ---------------------------------------------------------------------------
# 10. Convergence detection
# ---------------------------------------------------------------------------


class TestConvergence:
    def test_converged_when_scores_tight(self):
        """Top 3 within 2% = converged."""
        engine = EvolveEngine()
        config = EvolveConfig()
        pop = [
            EvolveCandidate(
                id=f"c_{i}", experiment_id="exp", artifact_type="prompt",
                content="text", metrics={"q": 0.90 + i * 0.005},
            )
            for i in range(5)
        ]
        assert engine._converged(pop, config) is True

    def test_not_converged_when_scores_spread(self):
        """Top 3 with > 2% spread = not converged."""
        engine = EvolveEngine()
        config = EvolveConfig()
        pop = [
            EvolveCandidate(
                id="c_0", experiment_id="exp", artifact_type="prompt",
                content="text", metrics={"q": 0.90},
            ),
            EvolveCandidate(
                id="c_1", experiment_id="exp", artifact_type="prompt",
                content="text", metrics={"q": 0.70},
            ),
            EvolveCandidate(
                id="c_2", experiment_id="exp", artifact_type="prompt",
                content="text", metrics={"q": 0.50},
            ),
        ]
        assert engine._converged(pop, config) is False

    def test_not_converged_with_few_evaluated(self):
        engine = EvolveEngine()
        config = EvolveConfig()
        pop = [
            EvolveCandidate(
                id="c_0", experiment_id="exp", artifact_type="prompt",
                content="text", metrics={"q": 0.9},
            ),
        ]
        assert engine._converged(pop, config) is False


# ---------------------------------------------------------------------------
# 11. Feature flag gating
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    @pytest.mark.asyncio
    async def test_disabled_feature_flag_raises(self):
        """When ALPHA_EVOLVE is off, evolve() should raise RuntimeError."""
        import shared.evolve_engine as mod

        original = mod.ALPHA_EVOLVE_ENABLED
        try:
            mod.ALPHA_EVOLVE_ENABLED = False
            engine = EvolveEngine(llm_generate=AsyncMock(return_value="x"))
            with pytest.raises(RuntimeError, match="disabled"):
                await engine.evolve(
                    seed="test",
                    artifact_type="prompt",
                    evaluator=AsyncMock(return_value={"q": 1.0}),
                )
        finally:
            mod.ALPHA_EVOLVE_ENABLED = original


# ---------------------------------------------------------------------------
# 12. Artifact wiring in learning_loop
# ---------------------------------------------------------------------------


_has_numpy = True
try:
    import numpy  # noqa: F401
except ImportError:
    _has_numpy = False


@pytest.mark.skipif(not _has_numpy, reason="numpy not installed")
class TestLearningLoopWiring:
    @pytest.mark.asyncio
    async def test_evolve_email_template_exists(self):
        """The wiring function should be importable."""
        from titan.neuro.learning_loop import evolve_email_template
        assert callable(evolve_email_template)

    @pytest.mark.asyncio
    async def test_evolve_scoring_rubric_exists(self):
        from titan.neuro.learning_loop import evolve_scoring_rubric
        assert callable(evolve_scoring_rubric)

    @pytest.mark.asyncio
    @patch("shared.evolve_engine.ALPHA_EVOLVE_ENABLED", False)
    async def test_evolve_email_returns_none_when_disabled(self):
        from titan.neuro.learning_loop import evolve_email_template
        result = await evolve_email_template("seed template")
        assert result is None

    @pytest.mark.asyncio
    @patch("shared.evolve_engine.ALPHA_EVOLVE_ENABLED", False)
    async def test_evolve_rubric_returns_none_when_disabled(self):
        from titan.neuro.learning_loop import evolve_scoring_rubric
        result = await evolve_scoring_rubric("seed rubric")
        assert result is None


# ---------------------------------------------------------------------------
# 13. Safety constant checks
# ---------------------------------------------------------------------------


class TestSafetyConstants:
    def test_hard_boundaries(self):
        """Verify hard safety constants are set correctly."""
        assert MAX_MUTATION_TOKENS == 50
        assert MAX_DAILY_RUNS == 5
        assert COST_CAP_PER_RUN == 0.20
        assert RISK_THRESHOLD == 0.7
        assert "wallet.py" in PROTECTED_PATHS
        assert "security/" in PROTECTED_PATHS
        assert "middleware.py" in PROTECTED_PATHS
        assert "llm_client.py" in PROTECTED_PATHS
