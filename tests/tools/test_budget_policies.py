"""Tests for tools/budget_guard.py — Multi-Scope Budget Policies (Paperclip Pattern 7)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.budget_guard import (
    BudgetDecision,
    PolicyResult,
    _query_spend_for_policy,
    evaluate_policies,
)


def _make_policy_row(
    policy_id=1,
    scope_type="company",
    scope_value="objective_hertz",
    window_kind="monthly",
    limit_usd=800.0,
    warn_percent=80,
    hard_stop_enabled=True,
):
    """Build a dict mimicking a DB row from budget_policies."""
    return {
        "policy_id": policy_id,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "window_kind": window_kind,
        "limit_usd": limit_usd,
        "warn_percent": warn_percent,
        "hard_stop_enabled": hard_stop_enabled,
    }


# ---------------------------------------------------------------------------
# evaluate_policies — legacy fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_policies_legacy_fallback():
    """Flag off -> uses legacy get_month_spending."""
    mock_spending = AsyncMock(return_value={
        "total_spent": 400.0,
        "remaining": 400.0,
        "percent_used": 50.0,
        "exceeded": False,
        "categories": [],
    })
    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "false"}):
        with patch("tools.budget_guard.get_month_spending", mock_spending):
            decision = await evaluate_policies()
            assert decision.allowed is True
            assert decision.policies_evaluated == []
            mock_spending.assert_called_once()


@pytest.mark.asyncio
async def test_evaluate_policies_legacy_exceeded():
    """Legacy path returns allowed=False when exceeded."""
    mock_spending = AsyncMock(return_value={
        "total_spent": 850.0,
        "remaining": -50.0,
        "percent_used": 106.3,
        "exceeded": True,
        "categories": [],
    })
    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "false"}):
        with patch("tools.budget_guard.get_month_spending", mock_spending):
            decision = await evaluate_policies()
            assert decision.allowed is False


# ---------------------------------------------------------------------------
# evaluate_policies — multi-scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_policies_company_scope():
    """Company policy at 50% -> allowed."""
    company_policy = _make_policy_row(limit_usd=800)
    mock_fetch_all = AsyncMock(return_value=[company_policy])
    mock_fetch_val = AsyncMock(return_value=400.0)

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true", "PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with patch("tools.budget_guard.fetch_val", mock_fetch_val):
                decision = await evaluate_policies()
                assert decision.allowed is True
                assert len(decision.policies_evaluated) == 1
                assert decision.most_restrictive.percent_used == 50.0


@pytest.mark.asyncio
async def test_evaluate_policies_company_hard_stop():
    """Company at 100% -> blocked."""
    company_policy = _make_policy_row(limit_usd=800)
    mock_fetch_all = AsyncMock(return_value=[company_policy])
    mock_fetch_val = AsyncMock(return_value=850.0)  # Over limit

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true", "PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with patch("tools.budget_guard.fetch_val", mock_fetch_val):
                decision = await evaluate_policies()
                assert decision.allowed is False
                assert "Hard stop" in decision.reason


@pytest.mark.asyncio
async def test_evaluate_policies_agent_scope_filters():
    """Agent policy only applies to matching agent."""
    policies = [
        _make_policy_row(policy_id=1, scope_type="company", scope_value="objective_hertz", limit_usd=800),
        _make_policy_row(policy_id=2, scope_type="agent", scope_value="titan", limit_usd=200),
    ]
    mock_fetch_all = AsyncMock(return_value=policies)
    mock_fetch_val = AsyncMock(return_value=50.0)  # Under both limits

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true", "PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with patch("tools.budget_guard.fetch_val", mock_fetch_val):
                # hermes should skip titan policy
                decision = await evaluate_policies(agent_id="hermes")
                assert decision.allowed is True
                assert len(decision.policies_evaluated) == 1  # Only company


@pytest.mark.asyncio
async def test_evaluate_policies_most_restrictive_wins():
    """3 policies, tightest one is most_restrictive."""
    policies = [
        _make_policy_row(policy_id=1, scope_type="company", limit_usd=800),
        _make_policy_row(policy_id=2, scope_type="agent", scope_value="titan", limit_usd=200),
        _make_policy_row(policy_id=3, scope_type="pipeline_stage", scope_value="email_compose", limit_usd=100),
    ]
    mock_fetch_all = AsyncMock(return_value=policies)
    mock_fetch_val = AsyncMock(return_value=50.0)  # Same spend for all

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true", "PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with patch("tools.budget_guard.fetch_val", mock_fetch_val):
                decision = await evaluate_policies(agent_id="titan", pipeline_stage="email_compose")
                assert decision.allowed is True
                assert len(decision.policies_evaluated) == 3
                # pipeline_stage with $100 limit has lowest remaining ($50)
                assert decision.most_restrictive.scope_type == "pipeline_stage"
                assert decision.most_restrictive.remaining_usd == 50.0


@pytest.mark.asyncio
async def test_evaluate_policies_warn_emits_event():
    """80% usage -> emits budget_warning event."""
    company_policy = _make_policy_row(limit_usd=800, warn_percent=80)
    mock_fetch_all = AsyncMock(return_value=[company_policy])
    mock_fetch_val = AsyncMock(return_value=650.0)  # 81.25%
    mock_emit = AsyncMock()

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true", "PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with patch("tools.budget_guard.fetch_val", mock_fetch_val):
                with patch("shared.db.emit_event", mock_emit):
                    decision = await evaluate_policies()
                    assert decision.allowed is True
                    assert len(decision.warnings) == 1
                    assert "Budget warning" in decision.warnings[0]
                    mock_emit.assert_called_once()


@pytest.mark.asyncio
async def test_evaluate_policies_no_policies_allows():
    """Empty table -> allowed with warning."""
    mock_fetch_all = AsyncMock(return_value=[])

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            decision = await evaluate_policies()
            assert decision.allowed is True
            assert "No budget policies defined" in decision.warnings[0]


@pytest.mark.asyncio
async def test_evaluate_policies_db_error_propagates():
    """DB failure raises (callers handle)."""
    mock_fetch_all = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch.dict("os.environ", {"MULTI_SCOPE_BUDGET_ENABLED": "true"}):
        with patch("tools.budget_guard.fetch_all", mock_fetch_all):
            with pytest.raises(RuntimeError, match="DB down"):
                await evaluate_policies()


# ---------------------------------------------------------------------------
# _query_spend_for_policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_spend_for_policy_monthly():
    """Monthly window uses DATE_TRUNC."""
    mock_fv = AsyncMock(return_value=100.0)
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_val", mock_fv):
            result = await _query_spend_for_policy("company", "objective_hertz", "monthly")
            assert result == 100.0
            sql = mock_fv.call_args[0][0]
            assert "DATE_TRUNC" in sql


@pytest.mark.asyncio
async def test_query_spend_for_policy_lifetime():
    """Lifetime window has no time filter."""
    mock_fv = AsyncMock(return_value=500.0)
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_val", mock_fv):
            result = await _query_spend_for_policy("company", "objective_hertz", "lifetime")
            assert result == 500.0
            sql = mock_fv.call_args[0][0]
            assert "DATE_TRUNC" not in sql


@pytest.mark.asyncio
async def test_query_spend_uses_cost_events_when_enabled():
    """Flag on -> cost_events table."""
    mock_fv = AsyncMock(return_value=75.0)
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "true"}):
        with patch("tools.budget_guard.fetch_val", mock_fv):
            result = await _query_spend_for_policy("company", "objective_hertz", "monthly")
            assert result == 75.0
            sql = mock_fv.call_args[0][0]
            assert "cost_events" in sql


@pytest.mark.asyncio
async def test_query_spend_uses_legacy_when_disabled():
    """Flag off -> llm_metrics/budget_tracking."""
    mock_fv = AsyncMock(return_value=60.0)
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("tools.budget_guard.fetch_val", mock_fv):
            result = await _query_spend_for_policy("agent", "titan", "monthly")
            assert result == 60.0
            sql = mock_fv.call_args[0][0]
            assert "llm_metrics" in sql
            assert "daemon = %s" in sql
