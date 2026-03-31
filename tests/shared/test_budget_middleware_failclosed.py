"""Tests for budget_check_middleware — AEGIS fail-closed + pre-execution gate (Phase 13)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from shared.middleware import budget_check_middleware


async def _identity_handler(ctx):
    """Pass-through handler for testing middleware."""
    return {"success": True, "output": "ok"}


# ---------------------------------------------------------------------------
# AEGIS fail-closed — legacy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_middleware_db_error_rejects_legacy():
    """DB error in legacy path -> fail closed (AEGIS)."""
    with patch.dict("os.environ", {
        "ENABLE_CONSOLIDATED_BUDGET": "false",
        "PRE_EXECUTION_BUDGET_GATE_ENABLED": "false",
    }):
        with patch("shared.observability.get_metrics_summary", AsyncMock(side_effect=RuntimeError("DB down"))):
            result = await budget_check_middleware({"stage_name": "test"}, _identity_handler)
            assert result["success"] is False
            assert "fail closed" in result["output"]
            assert result.get("budget_blocked") is True


@pytest.mark.asyncio
async def test_budget_middleware_legacy_blocks_at_cap():
    """$800 exceeded -> blocked (existing behavior preserved)."""
    mock_summary = AsyncMock(return_value={
        "daemons": [{"total_cost_usd": 850.0}],
    })
    with patch.dict("os.environ", {
        "ENABLE_CONSOLIDATED_BUDGET": "false",
        "PRE_EXECUTION_BUDGET_GATE_ENABLED": "false",
    }):
        with patch("shared.observability.get_metrics_summary", mock_summary):
            result = await budget_check_middleware({"stage_name": "test"}, _identity_handler)
            assert result["success"] is False
            assert "Budget cap exceeded" in result["output"]


@pytest.mark.asyncio
async def test_budget_middleware_legacy_allows_under_cap():
    """Under cap -> passes through."""
    mock_summary = AsyncMock(return_value={
        "daemons": [{"total_cost_usd": 300.0}],
    })
    with patch.dict("os.environ", {
        "ENABLE_CONSOLIDATED_BUDGET": "false",
        "PRE_EXECUTION_BUDGET_GATE_ENABLED": "false",
    }):
        with patch("shared.observability.get_metrics_summary", mock_summary):
            result = await budget_check_middleware({"stage_name": "test"}, _identity_handler)
            assert result["success"] is True


# ---------------------------------------------------------------------------
# AEGIS fail-closed — pre-gate path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_middleware_db_error_rejects_pregate():
    """DB error in pre-gate -> fail closed."""
    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", AsyncMock(side_effect=RuntimeError("DB"))):
            ctx = {"stage_name": "email_compose", "daemon_name": "titan"}
            result = await budget_check_middleware(ctx, _identity_handler)
            assert result["success"] is False
            assert "fail closed" in result["output"]
            assert ctx.get("budget_fallback_to_ollama") is True


# ---------------------------------------------------------------------------
# Pre-gate cost estimation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_middleware_pregate_estimates_cost():
    """Estimated cost checked against remaining."""
    from tools.budget_guard import BudgetDecision, PolicyResult

    mock_avg = AsyncMock(return_value=0.03)
    mock_policy = PolicyResult(
        policy_id=1, scope_type="company", scope_value="oh",
        window_kind="monthly", limit_usd=800, spent_usd=400,
        remaining_usd=400, percent_used=50.0,
        warn_triggered=False, hard_stop=False,
    )
    mock_decision = BudgetDecision(
        allowed=True, reason="ok",
        policies_evaluated=[mock_policy],
        most_restrictive=mock_policy,
        warnings=[],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "email_compose", "daemon_name": "titan"}
                result = await budget_check_middleware(ctx, _identity_handler)
                assert result["success"] is True
                assert ctx.get("budget_remaining") == 400.0


@pytest.mark.asyncio
async def test_budget_middleware_pregate_rejects_over_budget():
    """Estimated > remaining -> blocked."""
    from tools.budget_guard import BudgetDecision, PolicyResult

    mock_avg = AsyncMock(return_value=5.0)  # $5 estimated
    mock_policy = PolicyResult(
        policy_id=1, scope_type="company", scope_value="oh",
        window_kind="monthly", limit_usd=800, spent_usd=797,
        remaining_usd=3.0, percent_used=99.6,
        warn_triggered=True, hard_stop=False,
    )
    mock_decision = BudgetDecision(
        allowed=True, reason="ok",
        policies_evaluated=[mock_policy],
        most_restrictive=mock_policy,
        warnings=[],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "email_compose", "daemon_name": "titan"}
                result = await budget_check_middleware(ctx, _identity_handler)
                assert result["success"] is False
                assert result.get("budget_blocked") is True
                assert ctx.get("budget_fallback_to_ollama") is True


@pytest.mark.asyncio
async def test_budget_middleware_pregate_allows_within_budget():
    """Estimated < remaining -> allowed."""
    from tools.budget_guard import BudgetDecision, PolicyResult

    mock_avg = AsyncMock(return_value=0.01)
    mock_policy = PolicyResult(
        policy_id=1, scope_type="company", scope_value="oh",
        window_kind="monthly", limit_usd=800, spent_usd=400,
        remaining_usd=400, percent_used=50.0,
        warn_triggered=False, hard_stop=False,
    )
    mock_decision = BudgetDecision(
        allowed=True, reason="ok",
        policies_evaluated=[mock_policy],
        most_restrictive=mock_policy,
        warnings=[],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "email_compose", "daemon_name": "titan"}
                result = await budget_check_middleware(ctx, _identity_handler)
                assert result["success"] is True


@pytest.mark.asyncio
async def test_budget_middleware_pregate_default_estimate():
    """No historical data -> $0.05 conservative default."""
    from tools.budget_guard import BudgetDecision, PolicyResult

    mock_avg = AsyncMock(return_value=None)  # No historical data
    mock_policy = PolicyResult(
        policy_id=1, scope_type="company", scope_value="oh",
        window_kind="monthly", limit_usd=800, spent_usd=400,
        remaining_usd=400, percent_used=50.0,
        warn_triggered=False, hard_stop=False,
    )
    mock_decision = BudgetDecision(
        allowed=True, reason="ok",
        policies_evaluated=[mock_policy],
        most_restrictive=mock_policy,
        warnings=[],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "new_stage", "daemon_name": "titan"}
                result = await budget_check_middleware(ctx, _identity_handler)
                # $0.05 < $400 remaining -> allowed
                assert result["success"] is True


@pytest.mark.asyncio
async def test_budget_middleware_pregate_attaches_context():
    """budget_remaining in ctx after pass."""
    from tools.budget_guard import BudgetDecision, PolicyResult

    mock_avg = AsyncMock(return_value=0.01)
    mock_policy = PolicyResult(
        policy_id=1, scope_type="company", scope_value="oh",
        window_kind="monthly", limit_usd=800, spent_usd=200,
        remaining_usd=600, percent_used=25.0,
        warn_triggered=False, hard_stop=False,
    )
    mock_decision = BudgetDecision(
        allowed=True, reason="ok",
        policies_evaluated=[mock_policy],
        most_restrictive=mock_policy,
        warnings=["test warning"],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "test", "daemon_name": "titan"}
                await budget_check_middleware(ctx, _identity_handler)
                assert ctx["budget_remaining"] == 600.0
                assert ctx["budget_warnings"] == ["test warning"]


@pytest.mark.asyncio
async def test_budget_middleware_fallback_signal():
    """Blocked -> budget_fallback_to_ollama in ctx."""
    from tools.budget_guard import BudgetDecision

    mock_avg = AsyncMock(return_value=0.01)
    mock_decision = BudgetDecision(
        allowed=False,
        reason="Hard stop: company at 105%",
        policies_evaluated=[],
        most_restrictive=None,
        warnings=[],
    )
    mock_eval = AsyncMock(return_value=mock_decision)

    with patch.dict("os.environ", {"PRE_EXECUTION_BUDGET_GATE_ENABLED": "true"}):
        with patch("shared.cost_events.get_average_cost_by_task_type", mock_avg):
            with patch("tools.budget_guard.evaluate_policies", mock_eval):
                ctx = {"stage_name": "test", "daemon_name": "titan"}
                result = await budget_check_middleware(ctx, _identity_handler)
                assert result["success"] is False
                assert ctx.get("budget_fallback_to_ollama") is True
                assert result.get("fallback") == "ollama"
