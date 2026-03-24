"""
Risk-aware autonomy gate.

Replaces the binary review_mode (on/off after 10 sales) with graduated autonomy:
- Each action type has a risk level and a minimum sales threshold for autonomy
- Low-risk actions (discovery, research, drafting) run free immediately
- Medium-risk actions (sending emails) unlock after a few successful sales
- High-risk actions (proposals, invoices, deploys) unlock later
- Critical actions (large invoices, budget spend) require the most experience
- Value caps: even after unlocking, actions above a dollar threshold need approval

This means the system starts doing useful work on day 1 (discovering, researching,
drafting emails) while keeping human-in-the-loop for anything that touches real
money or external communication until confidence is earned.
"""

import logging
from decimal import Decimal
from typing import Optional

from shared import db

logger = logging.getLogger("perseus.risk_gate")

# In-memory cache of autonomy policies (refreshed on first call)
_policy_cache: dict[str, dict] = {}
_cache_loaded = False


async def _load_policies():
    """Load autonomy policies from DB into memory cache."""
    global _policy_cache, _cache_loaded
    try:
        rows = await db.fetch_all("SELECT * FROM autonomy_policy")
        _policy_cache = {row["action_type"]: dict(row) for row in rows}
        _cache_loaded = True
    except Exception as e:
        logger.debug(f"Could not load autonomy policies (using defaults): {e}")
        _cache_loaded = True


async def check_autonomy(
    action_type: str,
    value: Optional[float] = None,
) -> dict:
    """
    Check if an action can proceed autonomously.

    Returns:
        {
            "allowed": True/False,
            "reason": "why allowed or blocked",
            "risk_level": "none|low|medium|high|critical",
            "requires_approval": True/False (if not allowed),
        }
    """
    if not _cache_loaded:
        await _load_policies()

    policy = _policy_cache.get(action_type)
    if not policy:
        # Unknown action type — allow with low risk (don't block the system)
        return {
            "allowed": True,
            "reason": f"No policy for '{action_type}' — defaulting to autonomous",
            "risk_level": "low",
            "requires_approval": False,
        }

    risk_level = policy["risk_level"]

    # No-risk actions always proceed
    if risk_level == "none":
        return {
            "allowed": True,
            "reason": "No-risk action — always autonomous",
            "risk_level": "none",
            "requires_approval": False,
        }

    # Check sales threshold
    sales_completed = int(await db.get_config("sales_completed", 0) or 0)
    min_sales = policy["min_sales_for_auto"]

    if sales_completed < min_sales:
        return {
            "allowed": False,
            "reason": f"Need {min_sales} sales for autonomous {action_type} (have {sales_completed})",
            "risk_level": risk_level,
            "requires_approval": True,
        }

    # Check value cap
    max_value = policy.get("max_value_auto")
    if max_value is not None and value is not None:
        max_val = float(max_value)
        if value > max_val:
            return {
                "allowed": False,
                "reason": f"Value ${value:.2f} exceeds autonomous cap ${max_val:.2f} for {action_type}",
                "risk_level": risk_level,
                "requires_approval": True,
            }

    return {
        "allowed": True,
        "reason": f"Autonomous: {sales_completed} sales >= {min_sales} threshold",
        "risk_level": risk_level,
        "requires_approval": False,
    }


async def gate_or_queue(
    action_type: str,
    client_id: int,
    content: dict,
    value: Optional[float] = None,
    item_type: str = "email_draft",
) -> bool:
    """
    Either proceed (return True) or queue for review (return False).

    If the action needs approval, inserts into review_queue and emits an event.
    The caller should skip the action when False is returned.
    """
    result = await check_autonomy(action_type, value=value)

    if result["allowed"]:
        logger.debug(f"Risk gate PASS: {action_type} — {result['reason']}")
        return True

    # Queue for approval
    import json
    await db.execute(
        """INSERT INTO review_queue (item_type, client_id, content, status)
           VALUES (%s, %s, %s, 'pending_review')""",
        (item_type, client_id, json.dumps(content)),
    )
    await db.emit_event("review_needed", {
        "type": item_type,
        "action_type": action_type,
        "client_id": client_id,
        "risk_level": result["risk_level"],
        "reason": result["reason"],
    })
    logger.info(f"Risk gate HOLD: {action_type} for client {client_id} — {result['reason']}")
    return False


async def refresh_policies():
    """Force reload policies from DB (call after admin changes)."""
    global _cache_loaded
    _cache_loaded = False
    await _load_policies()
