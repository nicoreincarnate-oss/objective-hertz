"""Phase 31 tests: UGO lambda tuning via bandit."""
from __future__ import annotations

import pytest


class TestUGOLambdaArms:
    def test_arms_defined(self):
        from shared.bandit import UGO_LAMBDA_ARMS
        assert "default" in UGO_LAMBDA_ARMS
        assert "cost_aggressive" in UGO_LAMBDA_ARMS
        assert "exploration_friendly" in UGO_LAMBDA_ARMS

    def test_arms_have_required_keys(self):
        from shared.bandit import UGO_LAMBDA_ARMS
        for name, arm in UGO_LAMBDA_ARMS.items():
            assert "cost" in arm, f"{name} missing 'cost'"
            assert "uncertainty" in arm, f"{name} missing 'uncertainty'"
            assert "redundancy" in arm, f"{name} missing 'redundancy'"

    @pytest.mark.asyncio
    async def test_select_returns_valid_lambdas(self):
        from shared.bandit import select_ugo_lambdas
        lambdas = await select_ugo_lambdas()
        assert "cost" in lambdas
        assert "uncertainty" in lambdas
        assert "redundancy" in lambdas
        assert 0.0 <= lambdas["cost"] <= 1.0
        assert 0.0 <= lambdas["uncertainty"] <= 1.0
        assert 0.0 <= lambdas["redundancy"] <= 1.0

    @pytest.mark.asyncio
    async def test_update_does_not_raise(self):
        from shared.bandit import update_ugo_lambdas
        # Should not raise even without DB
        await update_ugo_lambdas("default", task_completed=True, normalized_cost=0.5)

    @pytest.mark.asyncio
    async def test_update_failure_capped_reward(self):
        from shared.bandit import update_ugo_lambdas
        await update_ugo_lambdas("default", task_completed=False, normalized_cost=0.01)


class TestUGOExperimentId:
    def test_experiment_id(self):
        from shared.bandit import UGO_LAMBDA_EXPERIMENT
        assert UGO_LAMBDA_EXPERIMENT == "ugo_lambda_tuning"
