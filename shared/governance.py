"""Governance / Approval System -- approval workflow for sensitive actions.

Source: Paperclip schema/approvals.ts (MIT)
Feature flag: GOVERNANCE_ENABLED

Approval types:
- budget_override: spending above configured threshold
- autonomy_transition: review_mode True->False (AEGIS finding)
- config_change: system_config modifications to protected keys
- capability_grant: new daemon capabilities or tool access
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from shared import db

logger = logging.getLogger("shared.governance")

# Keys in system_config that require approval to change
PROTECTED_CONFIG_KEYS = frozenset({
    "review_mode",
    "max_daily_spend_usd",
    "max_task_depth",
    "enable_middleware",
    "email_send_enabled",
})

# Action types that always require approval
APPROVAL_REQUIRED_ACTIONS = frozenset({
    "autonomy_transition",
    "budget_override",
    "capability_grant",
})


def _enabled() -> bool:
    return os.environ.get("GOVERNANCE_ENABLED", "").lower() in ("true", "1")


async def request_approval(
    approval_type: str,
    details: dict[str, Any],
    requested_by: str,
    expires_hours: int = 24,
) -> str | None:
    """Create a pending approval request. Returns approval_id or None if flag is off.

    Also emits an event for Hermes to send Telegram notification.
    """
    if not _enabled():
        return None

    valid_types = ("budget_override", "autonomy_transition", "config_change", "capability_grant")
    if approval_type not in valid_types:
        raise ValueError(f"Invalid approval type: {approval_type}. Must be one of {valid_types}")

    approval_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO approvals (approval_id, type, status, requested_by, details, expires_at)
           VALUES (%s, %s, 'pending', %s, %s, NOW() + make_interval(hours => %s))""",
        (approval_id, approval_type, requested_by, Jsonb(details), expires_hours),
    )

    # Emit event for Hermes alert dispatch
    await db.emit_event("approval_requested", {
        "approval_id": approval_id,
        "type": approval_type,
        "requested_by": requested_by,
        "details": details,
    })

    logger.info(
        "Approval requested: %s by %s (id=%s, expires=%dh)",
        approval_type, requested_by, approval_id[:8], expires_hours,
    )
    return approval_id


async def resolve_approval(
    approval_id: str,
    decision: str,
    resolved_by: str,
) -> bool:
    """Approve or reject a pending approval. Returns True if resolved.

    decision must be 'approved' or 'rejected'.
    """
    if not _enabled():
        return False

    if decision not in ("approved", "rejected"):
        raise ValueError(f"Invalid decision: {decision}. Must be 'approved' or 'rejected'")

    result = await db.fetch_one(
        """UPDATE approvals
           SET status = %s, resolved_by = %s, resolved_at = NOW()
           WHERE approval_id = %s AND status = 'pending'
           RETURNING approval_id""",
        (decision, resolved_by, approval_id),
    )

    if result:
        await db.emit_event("approval_resolved", {
            "approval_id": approval_id,
            "decision": decision,
            "resolved_by": resolved_by,
        })
        logger.info("Approval %s %s by %s", approval_id[:8], decision, resolved_by)

    return result is not None


async def check_approval_required(action_type: str, config_key: str | None = None) -> bool:
    """Check if an action requires approval per policy.

    Returns True if approval is needed, False if the action can proceed.
    Always returns False when GOVERNANCE_ENABLED is off.
    """
    if not _enabled():
        return False

    # Always-require types
    if action_type in APPROVAL_REQUIRED_ACTIONS:
        return True

    # Config changes to protected keys
    if action_type == "config_change" and config_key in PROTECTED_CONFIG_KEYS:
        return True

    return False


async def check_pending_approval(approval_type: str, requested_by: str) -> dict[str, Any] | None:
    """Check if there's already a pending approval of this type from this requester.

    Prevents duplicate approval requests.
    """
    if not _enabled():
        return None

    return await db.fetch_one(
        """SELECT * FROM approvals
           WHERE type = %s AND requested_by = %s AND status = 'pending'
             AND expires_at > NOW()
           ORDER BY created_at DESC LIMIT 1""",
        (approval_type, requested_by),
    )


async def get_approved(approval_id: str) -> bool:
    """Check if a specific approval has been granted.

    Returns True only if status is 'approved'.
    """
    if not _enabled():
        return True  # When governance is off, everything is auto-approved

    row = await db.fetch_one(
        "SELECT status FROM approvals WHERE approval_id = %s",
        (approval_id,),
    )
    return row is not None and row["status"] == "approved"


async def auto_expire_stale() -> int:
    """Expire pending approvals older than their expires_at. Returns count expired.

    Called by Perseus scheduler daily.
    """
    if not _enabled():
        return 0

    result = await db.fetch_all(
        """UPDATE approvals
           SET status = 'expired'
           WHERE status = 'pending' AND expires_at < NOW()
           RETURNING approval_id""",
    )
    count = len(result)
    if count > 0:
        logger.info("Auto-expired %d stale approvals", count)
    return count


async def get_pending_approvals() -> list[dict[str, Any]]:
    """Get all pending (non-expired) approvals for dashboard display."""
    if not _enabled():
        return []

    return await db.fetch_all(
        """SELECT approval_id, type, status, requested_by, details,
                  expires_at, created_at
           FROM approvals
           WHERE status = 'pending' AND expires_at > NOW()
           ORDER BY created_at DESC""",
    )


async def get_approval_history(limit: int = 50) -> list[dict[str, Any]]:
    """Get recent approval history (all statuses) for audit trail."""
    if not _enabled():
        return []

    return await db.fetch_all(
        """SELECT approval_id, type, status, requested_by, details,
                  resolved_by, resolved_at, expires_at, created_at
           FROM approvals
           ORDER BY created_at DESC
           LIMIT %s""",
        (limit,),
    )
