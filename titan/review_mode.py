"""
Review mode logic for first 10 sales.
Nico approves emails, proposals, and closes until confidence is established.
"""

import logging

from shared.db import fetch_all, fetch_one, execute, get_config, emit_event
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

    # Mark as approved
    await execute(
        """UPDATE review_queue SET status = 'approved', reviewer_notes = %s, reviewed_at = NOW()
           WHERE id = %s""",
        (notes, review_id),
    )

    # Trigger the action based on item type
    if item["item_type"] == "email_draft":
        # Send the approved email
        await _send_approved_email(item)
    elif item["item_type"] == "proposal":
        # Send the approved proposal
        await _send_approved_proposal(item)

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
    """Add an approved email lead to the Instantly campaign."""
    import json
    content = item["content"] if isinstance(item["content"], dict) else json.loads(item["content"])

    try:
        from tools.instantly_client import InstantlyClient

        lead = await fetch_one(
            "SELECT email, business_name, contact_name, industry, city, country FROM clients WHERE id = %s",
            (item["client_id"],),
        )
        if not lead:
            return

        campaign_id = await get_config("instantly_campaign_id", "")
        if not campaign_id:
            # Create campaign if needed (import from email_send)
            from titan.pipeline.email_send import _get_or_create_campaign
            campaign_id = await _get_or_create_campaign()

        if not campaign_id:
            logger.error("No Instantly campaign available for approved email")
            return

        client = InstantlyClient()
        await client.add_lead(
            campaign_id=campaign_id,
            email=lead["email"],
            first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
            company_name=lead.get("business_name", ""),
            personalization=content.get("body", "")[:500],
            custom_subject=content.get("subject", ""),
        )
        await client.close()

        await transition_lead(item["client_id"], "email_sent")
        await execute(
            "UPDATE clients SET last_contact_at = NOW() WHERE id = %s",
            (item["client_id"],),
        )
        await execute(
            "UPDATE email_sequences SET status = 'sent', sent_at = NOW() WHERE client_id = %s AND step = 1",
            (item["client_id"],),
        )
    except Exception as e:
        logger.error(f"Failed to send approved email: {e}")


async def _send_approved_proposal(item: dict):
    """Add approved proposal lead to Instantly campaign for sending."""
    import json
    content = item["content"] if isinstance(item["content"], dict) else json.loads(item["content"])

    try:
        from tools.instantly_client import InstantlyClient

        lead = await fetch_one(
            "SELECT email, business_name, contact_name FROM clients WHERE id = %s",
            (item["client_id"],),
        )
        if not lead:
            return

        # Proposals go through a separate campaign or the same one
        campaign_id = await get_config("instantly_campaign_id", "")
        if not campaign_id:
            from titan.pipeline.email_send import _get_or_create_campaign
            campaign_id = await _get_or_create_campaign()

        if not campaign_id:
            logger.error("No Instantly campaign available for approved proposal")
            return

        client = InstantlyClient()
        await client.add_lead(
            campaign_id=campaign_id,
            email=lead["email"],
            first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
            company_name=lead.get("business_name", ""),
            personalization=content.get("body", "")[:500],
            custom_subject=content.get("subject", ""),
        )
        await client.close()

        await transition_lead(item["client_id"], "proposal_sent")
    except Exception as e:
        logger.error(f"Failed to send approved proposal: {e}")
