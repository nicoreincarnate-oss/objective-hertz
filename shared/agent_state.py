"""Agent State Machine — enum-based state and transition validation.

Defines the valid states an agent can be in and the legal transitions
between them. Used by AgentBase when AGENT_STATE_MACHINE_ENABLED is true.

Phase 12: Foundation Patterns (FP-01)
"""

from __future__ import annotations

from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    PAUSED = "paused"
    ERROR = "error"
    TERMINATED = "terminated"


class PauseReason(str, Enum):
    MANUAL = "manual"
    BUDGET = "budget"
    SYSTEM = "system"
    ERROR_THRESHOLD = "error_threshold"


# Valid transitions: from_state -> set of allowed to_states
TRANSITION_MATRIX: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {
        AgentState.PLANNING,
        AgentState.EXECUTING,
        AgentState.PAUSED,
        AgentState.TERMINATED,
    },
    AgentState.PLANNING: {
        AgentState.EXECUTING,
        AgentState.IDLE,
        AgentState.PAUSED,
        AgentState.ERROR,
    },
    AgentState.EXECUTING: {
        AgentState.REVIEWING,
        AgentState.IDLE,
        AgentState.PAUSED,
        AgentState.ERROR,
    },
    AgentState.REVIEWING: {
        AgentState.IDLE,
        AgentState.EXECUTING,
        AgentState.PAUSED,
        AgentState.ERROR,
    },
    AgentState.PAUSED: {
        AgentState.IDLE,
        AgentState.TERMINATED,
    },
    AgentState.ERROR: {
        AgentState.IDLE,
        AgentState.PAUSED,
        AgentState.TERMINATED,
    },
    AgentState.TERMINATED: set(),  # terminal state — no transitions out
}


def validate_transition(from_state: AgentState, to_state: AgentState) -> bool:
    """Return True if transition is allowed by the matrix."""
    return to_state in TRANSITION_MATRIX.get(from_state, set())
