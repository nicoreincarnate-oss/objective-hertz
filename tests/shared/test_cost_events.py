"""Tests for shared/cost_events.py — Per-Call Cost Events (Paperclip Pattern 8)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from shared.cost_events import (
    CostEvent,
    _cost_events_enabled,
    emit_cost_event,
    get_agent_spend,
    get_average_cost_by_task_type,
    get_total_spend_current_month,
)


# ---------------------------------------------------------------------------
# CostEvent dataclass
# ---------------------------------------------------------------------------


def test_cost_event_dataclass_defaults():
    """CostEvent has correct defaults for all optional fields."""
    ev = CostEvent(agent_id="titan", model="sonnet")
    assert ev.agent_id == "titan"
    assert ev.model == "sonnet"
    assert ev.tokens_in == 0
    assert ev.tokens_out == 0
    assert ev.cached_tokens == 0
    assert ev.cost_usd == 0.0
    assert ev.latency_ms == 0
    assert ev.task_id is None
    assert ev.task_type is None
    assert isinstance(ev.event_id, str)
    assert len(ev.event_id) == 32  # uuid4 hex


def test_cost_event_dataclass_full():
    """CostEvent accepts all fields."""
    ev = CostEvent(
        agent_id="hermes",
        model="haiku",
        tokens_in=100,
        tokens_out=200,
        cached_tokens=50,
        cost_usd=0.01,
        latency_ms=150,
        task_id="task-123",
        task_type="email_compose",
        event_id="deadbeef",
    )
    assert ev.tokens_in == 100
    assert ev.task_id == "task-123"
    assert ev.event_id == "deadbeef"


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def test_cost_events_enabled_default_false():
    """Feature flag defaults to false."""
    with patch.dict("os.environ", {}, clear=True):
        assert _cost_events_enabled() is False


def test_cost_events_enabled_true():
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "true"}):
        assert _cost_events_enabled() is True


# ---------------------------------------------------------------------------
# emit_cost_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_cost_event_respects_feature_flag():
    """Flag off -> no DB write."""
    mock_execute = AsyncMock()
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "false"}):
        with patch("shared.cost_events.execute", mock_execute, create=True):
            ev = CostEvent(agent_id="titan", model="sonnet", cost_usd=0.05)
            await emit_cost_event(ev)
            mock_execute.assert_not_called()


@pytest.mark.asyncio
async def test_emit_cost_event_writes_to_db():
    """Flag on -> DB write with correct params."""
    mock_execute = AsyncMock()
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "true"}):
        with patch("shared.db.execute", mock_execute):
            ev = CostEvent(
                agent_id="titan",
                model="sonnet",
                tokens_in=100,
                tokens_out=200,
                cost_usd=0.05,
                latency_ms=150,
                task_id="t-1",
                task_type="email_compose",
                event_id="abc123",
            )
            await emit_cost_event(ev)
            mock_execute.assert_called_once()
            args = mock_execute.call_args
            sql = args[0][0]
            params = args[0][1]
            assert "INSERT INTO cost_events" in sql
            assert params[0] == "abc123"  # event_id
            assert params[1] == "titan"  # agent_id
            assert params[4] == "sonnet"  # model
            assert params[8] == 0.05  # cost_usd


@pytest.mark.asyncio
async def test_emit_cost_event_swallows_db_error():
    """DB failure -> logs warning, no raise."""
    mock_execute = AsyncMock(side_effect=RuntimeError("DB down"))
    with patch.dict("os.environ", {"PER_CALL_COST_EVENTS_ENABLED": "true"}):
        with patch("shared.db.execute", mock_execute):
            ev = CostEvent(agent_id="titan", model="sonnet")
            # Should not raise
            await emit_cost_event(ev)


# ---------------------------------------------------------------------------
# get_average_cost_by_task_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_average_cost_by_task_type_returns_average():
    """Mock DB returns avg."""
    with patch("shared.db.fetch_val", AsyncMock(return_value=0.042)):
        result = await get_average_cost_by_task_type("email_compose")
        assert result == pytest.approx(0.042)


@pytest.mark.asyncio
async def test_get_average_cost_by_task_type_returns_none_no_data():
    """No rows -> None."""
    with patch("shared.db.fetch_val", AsyncMock(return_value=None)):
        result = await get_average_cost_by_task_type("unknown_type")
        assert result is None


@pytest.mark.asyncio
async def test_get_average_cost_by_task_type_swallows_error():
    """DB error -> None."""
    with patch("shared.db.fetch_val", AsyncMock(side_effect=RuntimeError("DB"))):
        result = await get_average_cost_by_task_type("email_compose")
        assert result is None


# ---------------------------------------------------------------------------
# get_total_spend_current_month
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_total_spend_current_month():
    """Verify monthly sum query returns float."""
    with patch("shared.db.fetch_val", AsyncMock(return_value=123.45)):
        result = await get_total_spend_current_month()
        assert result == pytest.approx(123.45)


@pytest.mark.asyncio
async def test_get_total_spend_current_month_error():
    """DB error returns 0.0."""
    with patch("shared.db.fetch_val", AsyncMock(side_effect=RuntimeError("DB"))):
        result = await get_total_spend_current_month()
        assert result == 0.0


# ---------------------------------------------------------------------------
# get_agent_spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_spend():
    """Verify agent-scoped sum query."""
    mock_fv = AsyncMock(return_value=55.5)
    with patch("shared.db.fetch_val", mock_fv):
        result = await get_agent_spend("titan")
        assert result == pytest.approx(55.5)
        # Verify parameterized query
        call_args = mock_fv.call_args[0]
        assert "agent_id = %s" in call_args[0]
        assert call_args[1][0] == "titan"


@pytest.mark.asyncio
async def test_get_agent_spend_error():
    """DB error returns 0.0."""
    with patch("shared.db.fetch_val", AsyncMock(side_effect=RuntimeError("DB"))):
        result = await get_agent_spend("titan")
        assert result == 0.0
