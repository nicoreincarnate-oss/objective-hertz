"""Behavioral eval: agent state machine rejects invalid transitions.

Uses shared.agent_state (the agent lifecycle state machine), not titan.state_machine
(which is the lead pipeline state machine).
"""

from __future__ import annotations

import pytest
from shared.agent_state import AgentState, validate_transition


# These transitions MUST be rejected per the transition matrix
INVALID_TRANSITIONS = [
    (AgentState.TERMINATED, AgentState.EXECUTING),  # Terminal state -- no escape
    (AgentState.TERMINATED, AgentState.IDLE),        # Terminal state -- no escape
    (AgentState.TERMINATED, AgentState.PLANNING),    # Terminal state -- no escape
    (AgentState.EXECUTING, AgentState.PLANNING),     # Must go through IDLE
    (AgentState.PLANNING, AgentState.REVIEWING),     # Must execute first
]

# These transitions MUST be allowed
VALID_TRANSITIONS = [
    (AgentState.IDLE, AgentState.EXECUTING),
    (AgentState.IDLE, AgentState.PLANNING),
    (AgentState.EXECUTING, AgentState.IDLE),
    (AgentState.EXECUTING, AgentState.PAUSED),
    (AgentState.EXECUTING, AgentState.ERROR),
    (AgentState.PAUSED, AgentState.IDLE),
    (AgentState.PAUSED, AgentState.TERMINATED),
    (AgentState.IDLE, AgentState.TERMINATED),
    (AgentState.ERROR, AgentState.IDLE),
]


@pytest.mark.parametrize("from_state,to_state", INVALID_TRANSITIONS)
def test_state_machine_rejects_invalid_transition(from_state, to_state, eval_recorder):
    """Agent state machine must reject invalid state transitions."""
    result = validate_transition(from_state, to_state)

    assert result is False, (
        f"State machine MUST reject transition {from_state.value} -> {to_state.value}. "
        "Invalid transitions break daemon lifecycle guarantees."
    )


@pytest.mark.parametrize("from_state,to_state", VALID_TRANSITIONS)
def test_state_machine_allows_valid_transition(from_state, to_state, eval_recorder):
    """Agent state machine must allow valid state transitions."""
    result = validate_transition(from_state, to_state)

    assert result is True, (
        f"State machine should allow transition {from_state.value} -> {to_state.value}."
    )


def test_terminated_is_truly_terminal(eval_recorder):
    """TERMINATED state must have no outgoing transitions."""
    from shared.agent_state import TRANSITION_MATRIX

    terminal_exits = TRANSITION_MATRIX.get(AgentState.TERMINATED, set())
    assert len(terminal_exits) == 0, (
        f"TERMINATED must be a terminal state with no exits. Found: {terminal_exits}"
    )
