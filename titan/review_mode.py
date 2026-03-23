"""
Review mode logic for first 10 sales.
Nico approves emails, proposals, and closes until confidence is established.
"""

import logging

from shared.db import emit_event, execute, fetch_all, fetch_one, get_config, transaction
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.review")


async def is_review_mode() -> bool:
    """Check if we're still in review mode."""
    return await get_config("review_mode", True)


async def get_pending_reviews() -> list[dict]:
    """Get all items pending Nico's review."""
    return await fetch_all(
        """SELECT rq.*, c.business_name, c.email
           FROM review_queue rq
           JOIN clients c ON c.id = rq.client_id
           WHERE rq.status = 'pending_review'
           ORDER BY rq.created_at ASC"""
    )


async def approve_review(review_id: int, notes: str = ""):
    """Approve a review queue item. Triggers the next action."""
    item = await fetch_one(
        "SELECT * FROM review_queue WHERE id = %s", (review_id,)
    )
    if not item:
        return False

    action_succeeded = True
    if item["item_type"] == "email_draft":
        action_succeeded = await _send_approved_email(item)
    elif item["item_type"] == "proposal":
        action_succeeded = await _send_approved_proposal(item)

    if not action_succeeded:
        logger.error("Review %s send failed; leaving item pending_review", review_id)
        return False

    await execute(
        """UPDATE review_queue SET status = 'approved', reviewer_notes = %s, reviewed_at = NOW()
           WHERE id = %s""",
        (notes, review_id),
    )

    await emit_event("review_approved", {
        "review_id": review_id,
        "type": item["item_type"],
        "client_id": item["client_id"],
    })
    logger.info(f"Review {review_id} approved ({item['item_type']})")
    return True


async def reject_review(review_id: int, notes: str = ""):
    """Reject a review queue item."""
    await execute(
        """UPDATE review_queue SET status = 'rejected', reviewer_notes = %s, reviewed_at = NOW()
           WHERE id = %s""",
        (notes, review_id),
    )
    await emit_event("review_rejected", {"review_id": review_id})
    logger.info(f"Review {review_id} rejected")
    return True


async def _send_approved_email(item: dict):
    """Add an approved email lead to the Instantly campaign via compliance gate."""
    content, lead, success = await _send_approved_review_item(item)
    if not success:
        return False

    seq_id = content.get("seq_id")
    step = content.get("step", 1)

    async with transaction() as conn:
        if step == 1:
            transitioned = await transition_lead(item["client_id"], "email_sent", conn=conn)
            if not transitioned:
                logger.warning(
                    "Review send for client %s could not transition to email_sent; leaving sequence pending",
                    item["client_id"],
                )
                return False
            await conn.execute(
                """UPDATE clients
                   SET last_contact_at = NOW(), updated_at = NOW()
                   WHERE id = %s""",
                (item["client_id"],),
            )
        else:
            await conn.execute(
                "UPDATE clients SET last_contact_at = NOW(), updated_at = NOW() WHERE id = %s",
                (item["client_id"],),
            )

        if seq_id:
            await conn.execute(
                "UPDATE email_sequences SET status = 'sent', sent_at = NOW() WHERE id = %s",
                (seq_id,),
            )
        else:
            await conn.execute(
                """UPDATE email_sequences SET status = 'sent', sent_at = NOW()
                   WHERE client_id = %s AND step = %s AND status = 'pending'""",
                (item["client_id"], step),
            )

    return True


async def _send_approved_proposal(item: dict):
    """Add approved proposal lead to Instantly campaign via compliance gate."""
    _, _, success = await _send_approved_review_item(item)
    if success:
        await transition_lead(item["client_id"], "proposal_sent")
        return True
    return False


async def _send_approved_review_item(item: dict) -> tuple[dict, dict | None, bool]:
    """Send approved review content through the shared compliance send path."""
    import json

    from titan.compliance import send_to_instantly

    content = item["content"] if isinstance(item["content"], dict) else json.loads(item["content"])
    seq_id = content.get("seq_id")

    lead = await fetch_one(
        "SELECT email, business_name, contact_name FROM clients WHERE id = %s",
        (item["client_id"],),
    )
    if not lead:
        return content, None, False

    campaign_id = await get_config("instantly_campaign_id", "")
    if not campaign_id:
        from titan.pipeline.email_send import _get_or_create_campaign
        campaign_id = await _get_or_create_campaign()

    if not campaign_id:
        logger.error("No Instantly campaign available for approved review item")
        return content, lead, False

    success = await send_to_instantly(
        campaign_id=campaign_id,
        client_id=item["client_id"],
        email=lead["email"],
        subject=content.get("subject", ""),
        body=content.get("body", ""),
        seq_id=seq_id,
        message_type="proposal" if item.get("item_type") == "proposal" else "email",
        first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
        company_name=lead.get("business_name", ""),
    )
    return content, lead, success
