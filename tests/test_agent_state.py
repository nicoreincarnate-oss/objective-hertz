"""Tests for Agent State Machine (Phase 12: FP-01)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.agent_state import (
    AgentState,
    PauseReason,
    TRANSITION_MATRIX,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Pure state machine tests
# ---------------------------------------------------------------------------


def test_valid_transitions():
    """IDLE->PLANNING->EXECUTING->REVIEWING->IDLE all succeed."""
    chain = [
        (AgentState.IDLE, AgentState.PLANNING),
        (AgentState.PLANNING, AgentState.EXECUTING),
        (AgentState.EXECUTING, AgentState.REVIEWING),
        (AgentState.REVIEWING, AgentState.IDLE),
    ]
    for from_s, to_s in chain:
        assert validate_transition(from_s, to_s), f"{from_s} -> {to_s} should be valid"


def test_invalid_transition_rejected():
    """TERMINATED->IDLE is invalid (terminal state)."""
    assert not validate_transition(AgentState.TERMINATED, AgentState.IDLE)
    assert not validate_transition(AgentState.TERMINATED, AgentState.EXECUTING)


def test_paused_requires_reason():
    """PAUSED state can only transition to IDLE or TERMINATED."""
    allowed = TRANSITION_MATRIX[AgentState.PAUSED]
    assert AgentState.IDLE in allowed
    assert AgentState.TERMINATED in allowed
    assert AgentState.EXECUTING not in allowed


def test_error_to_idle_recovery():
    """ERROR->IDLE is a valid recovery path."""
    assert validate_transition(AgentState.ERROR, AgentState.IDLE)


def test_transition_matrix_completeness():
    """Every AgentState appears as a key in TRANSITION_MATRIX."""
    for state in AgentState:
        assert state in TRANSITION_MATRIX, f"{state} missing from TRANSITION_MATRIX"


# ---------------------------------------------------------------------------
# AgentBase._transition() integration tests
# ---------------------------------------------------------------------------


def _mock_db():
    mock = MagicMock()
    mock.execute = AsyncMock()
    mock.fetch_one = AsyncMock(return_value=None)
    mock.fetch_all = AsyncMock(return_value=[])
    mock.emit_event = AsyncMock(return_value=0)
    mock.close_pool = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_feature_flag_off_passthrough():
    """_transition() returns True when flag is off regardless of states."""
    with patch.dict(os.environ, {"AGENT_STATE_MACHINE_ENABLED": ""}, clear=False):
        with patch("shared.agent_base.db", _mock_db()):
            from shared.agent_base import AgentBase

            class _TestAgent(AgentBase):
                name = "test_agent"
                description = "test"

                async def start(self):
                    pass

                async def stop(self):
                    pass

                async def health_check(self):
                    return {}

            agent = _TestAgent()
            result = await agent._transition(AgentState.IDLE)
            assert result is True


@pytest.mark.asyncio
async def test_transition_updates_state():
    """Valid transition updates internal state when flag is on."""
    with patch.dict(os.environ, {"AGENT_STATE_MACHINE_ENABLED": "true"}, clear=False):
        with patch("shared.agent_base.db", _mock_db()):
            from shared.agent_base import AgentBase

            class _TestAgent(AgentBase):
                name = "test_state_agent"
                description = "test"

                async def start(self):
                    pass

                async def stop(self):
                    pass

                async def health_check(self):
                    return {}

            agent = _TestAgent()
            assert agent.state == AgentState.IDLE

            with patch.object(agent, "emit_event", new_callable=AsyncMock):
                result = await agent._transition(AgentState.EXECUTING)
                assert result is True
                assert agent.state == AgentState.EXECUTING
                assert agent.pause_reason is None
