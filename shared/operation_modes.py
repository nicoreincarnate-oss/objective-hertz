"""Named operation modes for the Objective Hertz runtime.

Modes control how autonomous the system behaves:
  - READONLY:    observation and reporting only, no mutations
  - SUPERVISED:  all actions need orchestrator review before execution
  - REVIEW:      first N actions need human approval, then supervised
  - AUTONOMOUS:  full capability within grants, no confirmation needed
  - ESCALATE:    everything goes to Telegram for human decision

Modes are stored in DB system_config and can be changed via Telegram command
or API. Transitioning FROM readonly/escalate TO autonomous requires a
confirmation token to prevent accidental escalation.

Integrates with CapabilityPolicy from openjarvis/security/capabilities.py.
"""

from __future__ import annotations

import logging
import secrets
from enum import Enum

logger = logging.getLogger(__name__)


class OperationMode(str, Enum):
    READONLY = "readonly"
    SUPERVISED = "supervised"
    REVIEW = "review"
    AUTONOMOUS = "autonomous"
    ESCALATE = "escalate"


# Tools classified as destructive (blocked in READONLY mode, flagged in SUPERVISED)
DESTRUCTIVE_TOOL_PATTERNS: set[str] = {
    "file:write",
    "code:execute",
    "channel:send",
    "schedule:create",
    "system:admin",
    "memory:write",
}

# Tools that are always safe (allowed in all modes including READONLY)
READ_ONLY_TOOL_PATTERNS: set[str] = {
    "file:read",
    "network:fetch",
    "memory:read",
}


# Per-mode capability matrix
# Keys: execute_tasks, send_emails, modify_data, deploy, spend_budget,
#        require_confirmation, require_escalation
MODE_CAPABILITIES: dict[OperationMode, dict[str, bool]] = {
    OperationMode.AUTONOMOUS: {
        "execute_tasks": True,
        "send_emails": True,
        "modify_data": True,
        "deploy": True,
        "spend_budget": True,
        "require_confirmation": False,
        "require_escalation": False,
    },
    OperationMode.SUPERVISED: {
        "execute_tasks": True,
        "send_emails": True,
        "modify_data": True,
        "deploy": True,
        "spend_budget": True,
        "require_confirmation": True,
        "require_escalation": False,
    },
    OperationMode.REVIEW: {
        "execute_tasks": True,
        "send_emails": False,
        "modify_data": True,
        "deploy": False,
        "spend_budget": False,
        "require_confirmation": True,
        "require_escalation": False,
    },
    OperationMode.READONLY: {
        "execute_tasks": False,
        "send_emails": False,
        "modify_data": False,
        "deploy": False,
        "spend_budget": False,
        "require_confirmation": True,
        "require_escalation": False,
    },
    OperationMode.ESCALATE: {
        "execute_tasks": False,
        "send_emails": False,
        "modify_data": False,
        "deploy": False,
        "spend_budget": False,
        "require_confirmation": True,
        "require_escalation": True,
    },
}

# Restricted modes that require a confirmation token to leave
_RESTRICTED_MODES: frozenset[OperationMode] = frozenset({
    OperationMode.READONLY,
    OperationMode.ESCALATE,
})

# Modes that can be entered without a confirmation token
_UNRESTRICTED_TARGETS: frozenset[OperationMode] = frozenset({
    OperationMode.READONLY,
    OperationMode.ESCALATE,
})

DEFAULT_MODE = OperationMode.AUTONOMOUS

_current_mode: OperationMode = DEFAULT_MODE
_pending_confirmation_token: str | None = None


def get_mode() -> OperationMode:
    """Get the current operation mode."""
    return _current_mode


def set_mode(
    mode: OperationMode,
    confirmation_token: str | None = None,
    changed_by: str = "system",
) -> str | None:
    """Set the operation mode.

    Entering READONLY or ESCALATE requires no confirmation.
    Leaving READONLY or ESCALATE to AUTONOMOUS requires a confirmation token.

    When a confirmation token is required but not provided, a new token is
    generated and returned. The caller must present that token in a subsequent
    call to complete the transition.

    Args:
        mode: The target operation mode.
        confirmation_token: Token from a previous set_mode call, required when
            transitioning from a restricted mode to autonomous.
        changed_by: Identifier for audit logging.

    Returns:
        None if the mode was set successfully, or a confirmation token string
        if confirmation is required to complete the transition.

    Raises:
        ValueError: If the confirmation token is invalid.
    """
    global _current_mode, _pending_confirmation_token

    old_mode = _current_mode

    # Entering a restricted mode (readonly/escalate) never needs confirmation
    if mode in _UNRESTRICTED_TARGETS:
        _pending_confirmation_token = None
        _current_mode = mode
        logger.info(
            "Operation mode changed: %s -> %s (by %s)",
            old_mode.value,
            mode.value,
            changed_by,
        )
        return None

    # Leaving a restricted mode requires confirmation
    if old_mode in _RESTRICTED_MODES and mode not in _RESTRICTED_MODES:
        if confirmation_token is None:
            # Generate a new token and require the caller to confirm
            token = secrets.token_hex(16)
            _pending_confirmation_token = token
            logger.info(
                "Confirmation required for mode transition: %s -> %s (token issued, by %s)",
                old_mode.value,
                mode.value,
                changed_by,
            )
            return token

        if (
            _pending_confirmation_token is None
            or confirmation_token != _pending_confirmation_token
        ):
            raise ValueError("Invalid confirmation token for mode transition")

        # Token matches -- allow the transition
        _pending_confirmation_token = None

    # Normal transition (between non-restricted modes, or confirmed)
    _current_mode = mode
    logger.info(
        "Operation mode changed: %s -> %s (by %s)",
        old_mode.value,
        mode.value,
        changed_by,
    )
    return None


def is_action_allowed(mode: OperationMode, tool_spec: str) -> bool:
    """Check if a tool action is permitted in the given mode.

    Args:
        mode: The operation mode to check against.
        tool_spec: A capability string like ``"file:write"`` or ``"memory:read"``.

    Returns:
        True if the tool action is allowed in the given mode.
    """
    # Read-only tools are always allowed
    if tool_spec in READ_ONLY_TOOL_PATTERNS:
        return True

    # In READONLY and ESCALATE, destructive tools are blocked
    if mode in (OperationMode.READONLY, OperationMode.ESCALATE):
        return False

    # In REVIEW mode, destructive tools that are emails/deploy/budget are blocked
    if mode == OperationMode.REVIEW:
        caps = MODE_CAPABILITIES[mode]
        # Map tool specs to capability keys for fine-grained control
        if tool_spec == "channel:send" and not caps["send_emails"]:
            return False
        if tool_spec == "system:admin" and not caps["deploy"]:
            return False

    # SUPERVISED and AUTONOMOUS allow all tools (SUPERVISED flags for review
    # at the orchestrator layer, but does not block here)
    return True


def check_capability(mode: OperationMode, capability: str) -> bool:
    """Check if a named capability is allowed in the given mode.

    Args:
        mode: The operation mode to check against.
        capability: A capability key like ``"execute_tasks"`` or ``"deploy"``.

    Returns:
        True if the capability is enabled, False otherwise (including for
        unknown capability names).
    """
    caps = MODE_CAPABILITIES.get(mode, MODE_CAPABILITIES[DEFAULT_MODE])
    return caps.get(capability, False)


def requires_escalation(mode: OperationMode) -> bool:
    """Check if the mode requires escalation to Telegram."""
    return MODE_CAPABILITIES.get(mode, {}).get("require_escalation", False)


def requires_confirmation(mode: OperationMode) -> bool:
    """Check if the mode requires orchestrator confirmation for actions."""
    return MODE_CAPABILITIES.get(mode, {}).get("require_confirmation", False)


# --- Async DB integration ---


async def load_mode_from_db() -> OperationMode:
    """Load the operation mode from Postgres system_config at startup."""
    global _current_mode
    try:
        from shared.db import get_config
        mode_str = await get_config("operation_mode", DEFAULT_MODE.value)
        _current_mode = OperationMode(mode_str)
    except (ValueError, Exception) as exc:
        logger.warning("Failed to read operation mode from DB, using default: %s", exc)
        _current_mode = DEFAULT_MODE
    return _current_mode


async def persist_mode(mode: OperationMode, changed_by: str = "system") -> None:
    """Persist the operation mode to Postgres and write audit log."""
    old_mode = _current_mode
    try:
        from shared.db import execute, set_config
        await set_config("operation_mode", mode.value)

        # Audit log (AEGIS compliance: config changes must log to config_audit_log)
        await execute(
            """INSERT INTO config_audit_log (config_key, old_value, new_value, changed_by)
               VALUES (%s, %s, %s, %s)""",
            ("operation_mode", old_mode.value, mode.value, changed_by),
        )
    except (ConnectionError, RuntimeError, OSError, ImportError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error("Failed to persist operation mode: %s", exc)
        raise

    logger.info(
        "Operation mode persisted to DB: %s -> %s (by %s)",
        old_mode.value,
        mode.value,
        changed_by,
    )


def reset_cache() -> None:
    """Reset internal state (for testing)."""
    global _current_mode, _pending_confirmation_token
    _current_mode = DEFAULT_MODE
    _pending_confirmation_token = None


__all__ = [
    "DEFAULT_MODE",
    "DESTRUCTIVE_TOOL_PATTERNS",
    "MODE_CAPABILITIES",
    "OperationMode",
    "READ_ONLY_TOOL_PATTERNS",
    "check_capability",
    "get_mode",
    "is_action_allowed",
    "load_mode_from_db",
    "persist_mode",
    "requires_confirmation",
    "requires_escalation",
    "reset_cache",
    "set_mode",
]
