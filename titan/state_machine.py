"""
Lead state machine for the Titan pipeline.
Defines valid transitions and ensures leads progress correctly.
"""

from typing import Optional

# Valid transitions: current_status -> list of valid next statuses
TRANSITIONS: dict[str, list[str]] = {
    "discovered": ["researched", "lost"],
    "researched": ["email_drafted", "lost"],
    "email_drafted": ["email_queued", "lost"],
    "email_queued": ["email_sent", "lost"],
    "email_sent": ["followed_up", "replied", "unresponsive", "lost"],
    "followed_up": ["replied", "unresponsive", "lost"],
    "replied": ["interested", "lost", "unsubscribed"],
    "interested": ["demo_built", "proposal_sent", "lost"],
    "demo_built": ["proposal_sent", "negotiating", "lost"],
    "proposal_sent": ["negotiating", "closed", "lost"],
    "negotiating": ["closed", "lost"],
    "closed": ["building"],
    "building": ["deployed"],
    "deployed": ["invoiced"],
    "invoiced": ["paid"],
    "paid": [],  # Terminal state — success!
    "lost": [],  # Terminal state — didn't work out
    "unresponsive": ["email_drafted"],  # Can retry later
    "unsubscribed": [],  # Terminal — respect their wish
}


def can_transition(current: str, target: str) -> bool:
    """Check if a status transition is valid."""
    return target in TRANSITIONS.get(current, [])


def valid_next_states(current: str) -> list[str]:
    """Get all valid next states from current status."""
    return TRANSITIONS.get(current, [])


async def transition_lead(client_id: int, new_status: str) -> bool:
    """
    Transition a lead to a new status.
    Returns True if successful, False if invalid transition.
    """
    import logging
    from shared.db import fetch_one, execute

    logger = logging.getLogger("perseus.titan.state_machine")

    lead = await fetch_one("SELECT status FROM clients WHERE id = %s", (client_id,))
    if not lead:
        logger.warning(f"Lead {client_id} not found")
        return False

    current = lead["status"]
    if not can_transition(current, new_status):
        logger.warning(f"Invalid transition for lead {client_id}: {current} → {new_status}")
        return False

    await execute(
        "UPDATE clients SET status = %s, updated_at = NOW() WHERE id = %s",
        (new_status, client_id),
    )
    logger.debug(f"Lead {client_id}: {current} → {new_status}")
    return True
