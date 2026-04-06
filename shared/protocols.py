"""Structured inter-agent protocol types (Phase 24, D-15).

Replaces unstructured text messages routed by task_type with
formal, typed protocol messages that carry correlation IDs,
timestamps, and structured payloads.

All inter-agent communication should use ProtocolMessage for
type safety, traceability, and easier debugging.

Task 24-03 (ANATOMY_TASK_RESILIENCE):
  - ``AgentTaskState`` — unified enum covering both A2A TaskState
    and AgentManager status values.
  - ``a2a_state_to_unified()`` / ``agent_status_to_unified()`` —
    mapping functions between the domain-specific values and the
    unified enum.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProtocolType(str, Enum):
    """Formal message types for inter-agent communication."""

    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    ESCALATION = "escalation"
    DELEGATION_REQUEST = "delegation_request"
    DELEGATION_RESULT = "delegation_result"
    HEALTH_CHECK = "health_check"
    HEALTH_RESPONSE = "health_response"
    SYNTHESIS_INSTRUCTION = "synthesis_instruction"
    HEARTBEAT_REQUEST = "heartbeat_request"
    HEARTBEAT_RESPONSE = "heartbeat_response"


@dataclass
class ProtocolMessage:
    """Typed inter-agent message.

    Attributes:
        type: The protocol message type.
        sender: Name/ID of the sending agent.
        recipient: Name/ID of the target agent.
        payload: Arbitrary structured data for the message.
        correlation_id: Links a request to its response (e.g. UUID).
        timestamp: Unix timestamp of message creation.
    """

    type: ProtocolType
    sender: str
    recipient: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""  # links request to response
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON/DB storage."""
        return {
            "protocol_type": self.type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProtocolMessage:
        """Deserialize from a plain dict."""
        return cls(
            type=ProtocolType(data["protocol_type"]),
            sender=data["sender"],
            recipient=data["recipient"],
            payload=data.get("payload", {}),
            correlation_id=data.get("correlation_id", ""),
            timestamp=data.get("timestamp", 0.0),
        )


def is_protocol_message(data: dict[str, Any]) -> bool:
    """Check if a dict is a structured protocol message.

    Returns True when the dict contains a ``protocol_type`` key
    whose value matches one of the known ProtocolType values.
    """
    return (
        isinstance(data, dict)
        and "protocol_type" in data
        and data["protocol_type"] in ProtocolType._value2member_map_
    )


# ── Protocol payload schemas (documentation + validation) ────────────

PROTOCOL_PAYLOAD_SCHEMAS: dict[ProtocolType, dict[str, str]] = {
    ProtocolType.SHUTDOWN_REQUEST: {
        "reason": "str -- why shutdown is requested",
        "deadline_seconds": "float -- seconds before force-kill (default 30)",
    },
    ProtocolType.SHUTDOWN_RESPONSE: {
        "acknowledged": "bool -- whether shutdown is accepted",
        "estimated_completion_seconds": "float -- time to finish current work",
        "current_task": "str | None -- task being finished",
    },
    ProtocolType.ESCALATION: {
        "severity": "str -- low|medium|high|critical",
        "problem": "str -- description of the issue",
        "context": "dict -- additional context",
        "from_agent": "str -- originating agent",
    },
    ProtocolType.DELEGATION_RESULT: {
        "original_task_id": "str -- task that was delegated",
        "status": "str -- completed|failed|partial",
        "result": "dict -- output data",
        "error": "str | None -- error message if failed",
    },
    ProtocolType.SYNTHESIS_INSTRUCTION: {
        "instruction": "str -- what the agent should do",
        "priority": "int -- 0=normal, 10=critical",
        "source_cycle_id": "str -- synthesis cycle that produced this",
        "expires_at": "float -- unix timestamp when instruction expires",
    },
}


def should_process_event(event_payload: dict, self_name: str) -> bool:
    """Check if an event should be processed by this agent.

    Returns False if the sender is the same as self_name (prevents self-processing)
    or if ``_exclude_sender`` matches self_name.
    """
    if not isinstance(event_payload, dict):
        return True
    sender = event_payload.get("sender", "")
    if sender and sender == self_name:
        return False
    exclude = event_payload.get("_exclude_sender", "")
    if exclude and exclude == self_name:
        return False
    return True


# ── Unified Agent/Task State (Task 24-03, ANATOMY_TASK_RESILIENCE) ────


class AgentTaskState(str, Enum):
    """Unified state enum covering both A2A TaskState and AgentManager status.

    A2A tasks track lifecycle via TaskState (submitted/working/completed/etc.)
    while AgentManager tracks agent status as free-form strings
    (idle/running/paused/archived).  These describe overlapping concerns
    with separate parallel state.  AgentTaskState provides a single
    vocabulary so both subsystems can reference each other.
    """

    IDLE = "idle"
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


def _is_unified_state_enabled() -> bool:
    """Return True when the ANATOMY_TASK_RESILIENCE flag is active."""
    return os.environ.get("ANATOMY_TASK_RESILIENCE", "").lower() in ("true", "1")


# ── A2A TaskState string values (mirror of openjarvis.a2a.protocol.TaskState) ──
_A2A_STATE_MAP: dict[str, AgentTaskState] = {
    "submitted": AgentTaskState.SUBMITTED,
    "working": AgentTaskState.WORKING,
    "input-required": AgentTaskState.INPUT_REQUIRED,
    "completed": AgentTaskState.COMPLETED,
    "canceled": AgentTaskState.CANCELLED,
    "failed": AgentTaskState.FAILED,
}

# ── AgentManager status string values ─────────────────────────────────
_AGENT_STATUS_MAP: dict[str, AgentTaskState] = {
    "idle": AgentTaskState.IDLE,
    "running": AgentTaskState.WORKING,
    "paused": AgentTaskState.PAUSED,
    "archived": AgentTaskState.ARCHIVED,
    "pending": AgentTaskState.SUBMITTED,
    "completed": AgentTaskState.COMPLETED,
    "failed": AgentTaskState.FAILED,
}


def a2a_state_to_unified(a2a_state: str) -> AgentTaskState:
    """Map an A2A TaskState value string to the unified AgentTaskState.

    Args:
        a2a_state: The ``.value`` of an ``openjarvis.a2a.protocol.TaskState``
                   member (e.g. ``"submitted"``, ``"input-required"``).

    Returns:
        The corresponding ``AgentTaskState`` member.

    Raises:
        ValueError: If *a2a_state* is not a recognised A2A state value.
    """
    unified = _A2A_STATE_MAP.get(a2a_state)
    if unified is None:
        raise ValueError(
            f"Unknown A2A state: {a2a_state!r}. "
            f"Known values: {sorted(_A2A_STATE_MAP)}"
        )
    return unified


def agent_status_to_unified(agent_status: str) -> AgentTaskState:
    """Map an AgentManager status string to the unified AgentTaskState.

    Args:
        agent_status: Status string stored in the ``managed_agents`` table
                      (e.g. ``"idle"``, ``"running"``, ``"paused"``).

    Returns:
        The corresponding ``AgentTaskState`` member.

    Raises:
        ValueError: If *agent_status* is not a recognised agent status value.
    """
    unified = _AGENT_STATUS_MAP.get(agent_status)
    if unified is None:
        raise ValueError(
            f"Unknown agent status: {agent_status!r}. "
            f"Known values: {sorted(_AGENT_STATUS_MAP)}"
        )
    return unified


def sync_agent_status_to_a2a(
    agent_status: str,
    a2a_task: Any | None = None,
) -> AgentTaskState | None:
    """When an agent status changes, update the linked A2A task state.

    Gated behind ``ANATOMY_TASK_RESILIENCE``.  If the flag is off or
    *a2a_task* is ``None``, returns ``None`` and does nothing.

    Args:
        agent_status: The new AgentManager status string.
        a2a_task: An ``openjarvis.a2a.protocol.A2ATask`` instance, or None.

    Returns:
        The new unified state if a sync occurred, else ``None``.
    """
    if not _is_unified_state_enabled():
        return None
    if a2a_task is None:
        return None

    unified = agent_status_to_unified(agent_status)

    # Reverse-map unified → closest A2A TaskState value
    _UNIFIED_TO_A2A: dict[AgentTaskState, str] = {
        AgentTaskState.IDLE: "submitted",
        AgentTaskState.SUBMITTED: "submitted",
        AgentTaskState.WORKING: "working",
        AgentTaskState.INPUT_REQUIRED: "input-required",
        AgentTaskState.COMPLETED: "completed",
        AgentTaskState.FAILED: "failed",
        AgentTaskState.PAUSED: "input-required",  # closest A2A equivalent
        AgentTaskState.CANCELLED: "canceled",
        AgentTaskState.ARCHIVED: "completed",  # archived implies done
    }

    new_a2a_value = _UNIFIED_TO_A2A.get(unified)
    if new_a2a_value is None:
        return None

    # Import locally to avoid circular imports
    from openjarvis.a2a.protocol import TaskState

    try:
        a2a_task.state = TaskState(new_a2a_value)
        logger.debug(
            "Synced agent status %r → A2A task %s state %r",
            agent_status,
            getattr(a2a_task, "task_id", "?"),
            new_a2a_value,
        )
    except ValueError:
        logger.warning(
            "Failed to sync agent status %r to A2A state: %r is not a valid TaskState",
            agent_status,
            new_a2a_value,
        )
        return None

    return unified
