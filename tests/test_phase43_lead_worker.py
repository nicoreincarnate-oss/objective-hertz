"""Phase 43: Lead/Worker loop tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_plan_fn():
    """Mock Lead planner that returns 3 steps."""
    from shared.lead_worker import Step
    async def _plan(goal: str, context: dict, prior: list) -> list[Step]:
        return [
            Step(id="s1", description="step 1"),
            Step(id="s2", description="step 2"),
            Step(id="s3", description="step 3"),
        ]
    return _plan


@pytest.fixture
def mock_execute_fn_success():
    """Mock Worker that succeeds every step."""
    from shared.lead_worker import StepResult
    call_count = {"n": 0}
    async def _execute(step, context: dict) -> StepResult:
        call_count["n"] += 1
        return StepResult(
            step_id=step.id, success=True,
            output=f"output_{step.id}", cost_usd=0.01,
        )
    _execute.call_count = call_count
    return _execute


@pytest.fixture
def mock_execute_fn_first_fail():
    """Mock Worker that fails the first step then succeeds."""
    from shared.lead_worker import StepResult
    state = {"called": 0}
    async def _execute(step, context: dict) -> StepResult:
        state["called"] += 1
        if state["called"] == 1:
            return StepResult(step_id=step.id, success=False, output="", error="boom")
        return StepResult(step_id=step.id, success=True, output=f"out_{step.id}", cost_usd=0.01)
    return _execute


class TestLeadWorkerLoop:
    @pytest.mark.asyncio
    async def test_runs_all_steps_on_success(self, mock_plan_fn, mock_execute_fn_success):
        from shared.lead_worker import LeadWorkerLoop, LeadWorkerConfig
        loop = LeadWorkerLoop(
            config=LeadWorkerConfig(),
            plan_fn=mock_plan_fn,
            execute_fn=mock_execute_fn_success,
        )
        result = await loop.run("goal", {})
        assert result.success is True
        assert result.steps_executed == 3
        assert result.steps_total == 3
        assert result.total_cost_usd == 0.03  # 3 × 0.01

    @pytest.mark.asyncio
    async def test_revises_plan_on_failure(self, mock_plan_fn, mock_execute_fn_first_fail):
        from shared.lead_worker import LeadWorkerLoop, LeadWorkerConfig
        loop = LeadWorkerLoop(
            config=LeadWorkerConfig(architect_revisits_on_failure=True),
            plan_fn=mock_plan_fn,
            execute_fn=mock_execute_fn_first_fail,
        )
        result = await loop.run("goal", {})
        # Failed first step, plan revised, more steps executed
        assert result.plan_revisions >= 1

    @pytest.mark.asyncio
    async def test_max_steps_enforced(self, mock_execute_fn_success):
        from shared.lead_worker import LeadWorkerLoop, LeadWorkerConfig, Step
        async def big_plan(goal, ctx, prior):
            return [Step(id=f"s{i}", description=f"step {i}") for i in range(50)]
        loop = LeadWorkerLoop(
            config=LeadWorkerConfig(max_steps=5),
            plan_fn=big_plan,
            execute_fn=mock_execute_fn_success,
        )
        result = await loop.run("goal", {})
        assert result.steps_executed == 5  # Capped at max_steps

    @pytest.mark.asyncio
    async def test_empty_plan_fails_gracefully(self, mock_execute_fn_success):
        from shared.lead_worker import LeadWorkerLoop, LeadWorkerConfig
        async def empty_plan(goal, ctx, prior):
            return []
        loop = LeadWorkerLoop(
            config=LeadWorkerConfig(),
            plan_fn=empty_plan,
            execute_fn=mock_execute_fn_success,
        )
        result = await loop.run("goal", {})
        assert result.success is False
        assert "empty plan" in result.error.lower()
