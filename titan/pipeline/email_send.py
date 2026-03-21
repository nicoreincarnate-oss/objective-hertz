"""
Stage 4: Email Sending via Instantly.ai

Instantly is campaign-based — we DON'T send individual emails.
Flow:
1. Ensure a Perseus campaign exists in Instantly
2. Add drafted leads to the campaign with personalized variables
3. Instantly handles sending, timing, warmup, and account rotation
4. We track what was queued and check analytics later

Review mode: leads queue for Nico's approval before being added to campaign.
"""

import json
import logging

from shared.db import fetch_all, fetch_one, execute, get_config, set_config, emit_event
from titan.state_machine import transition_lead
from titan.training import collect_training_example

logger = logging.getLogger("perseus.titan.email_send")

# Campaign name prefix — one active campaign at a time
CAMPAIGN_PREFIX = "perseus-outreach"


async def send_emails(batch_size: int = 50):
    """Add queued emails to Instantly campaign. Respects review mode."""
    # Check review mode
    review_mode = await get_config("review_mode", True)
    if review_mode:
        await _queue_for_review(batch_size)
        return

    # Get emails ready to send
    leads = await fetch_all(
        """SELECT c.id as client_id, c.email, c.business_name,
                  c.contact_name, c.industry, c.city, c.country,
                  es.id as seq_id, es.subject, es.body
           FROM clients c
           JOIN email_sequences es ON es.client_id = c.id
           WHERE c.status = 'email_drafted' AND es.status = 'pending' AND es.step = 1
           ORDER BY c.lead_score DESC LIMIT %s""",
        (batch_size,),
    )

    if not leads:
        return

    # Ensure we have an active campaign in Instantly
    campaign_id = await _get_or_create_campaign()
    if not campaign_id:
        logger.error("Could not get or create Instantly campaign")
        return

    added_count = 0
    for lead in leads:
        try:
            success = await _add_lead_to_campaign(campaign_id, lead)
            if success:
                added_count += 1
        except Exception as e:
            logger.error(f"Failed to add lead {lead['client_id']} to campaign: {e}")

    if added_count:
        await emit_event("emails_queued", {"count": added_count, "campaign_id": campaign_id})
        logger.info(f"Added {added_count} leads to Instantly campaign {campaign_id}")


async def _get_or_create_campaign() -> str:
    """Get the active Perseus campaign or create one."""
    # Check if we have a stored campaign ID
    campaign_id = await get_config("instantly_campaign_id", "")

    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()

        if campaign_id:
            # Verify it still exists
            try:
                campaign = await client.get_campaign(campaign_id)
                await client.close()
                return campaign_id
            except Exception:
                logger.info("Stored campaign no longer valid, creating new one")

        # Create a new campaign
        from datetime import datetime
        name = f"{CAMPAIGN_PREFIX}-{datetime.now().strftime('%Y%m%d')}"
        campaign = await client.create_campaign(name)
        new_id = campaign.get("id", "")
        await client.close()

        if new_id:
            await set_config("instantly_campaign_id", new_id)
            logger.info(f"Created Instantly campaign: {name} ({new_id})")

            # Activate the campaign so Instantly starts sending
            client2 = InstantlyClient()
            await client2.activate_campaign(new_id)
            await client2.close()

        return new_id

    except ImportError:
        logger.warning("Instantly client not available")
        return ""
    except Exception as e:
        logger.error(f"Failed to get/create campaign: {e}")
        return ""


async def _add_lead_to_campaign(campaign_id: str, lead: dict) -> bool:
    """Add a single lead to the Instantly campaign with personalized variables."""
    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()

        # Instantly uses custom variables in email templates
        # The email subject/body from our compose stage become the sequence
        # For now, we add leads with variables that Instantly's sequence can use
        await client.add_lead(
            campaign_id=campaign_id,
            email=lead["email"],
            first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
            company_name=lead.get("business_name", ""),
            personalization=lead.get("body", "")[:500],  # Custom email body as variable
            custom_subject=lead.get("subject", ""),
            industry=lead.get("industry", ""),
            city=lead.get("city", ""),
            country=lead.get("country", ""),
        )
        await client.close()

        # Update our tracking
        await execute(
            "UPDATE email_sequences SET status = 'sent', sent_at = NOW() WHERE id = %s",
            (lead["seq_id"],),
        )
        await execute(
            "UPDATE clients SET last_contact_at = NOW() WHERE id = %s",
            (lead["client_id"],),
        )
        await transition_lead(lead["client_id"], "email_sent")

        # Record training example (outcome filled later by follow_up)
        client_info = await fetch_one(
            "SELECT research_summary, industry, language FROM clients WHERE id = %s",
            (lead["client_id"],),
        )
        if client_info:
            await collect_training_example(
                example_type="email_compose",
                input_text=json.dumps({
                    "research": client_info.get("research_summary", ""),
                    "industry": client_info.get("industry", ""),
                    "language": client_info.get("language", "en"),
                }),
                output_text=json.dumps({
                    "subject": lead.get("subject", ""),
                    "body": lead.get("body", ""),
                }),
                outcome="",
                metadata={"email_seq_id": lead["seq_id"], "client_id": lead["client_id"]},
            )

        return True

    except ImportError:
        logger.warning("Instantly client not available")
        return False
    except Exception as e:
        logger.error(f"Instantly add lead failed: {e}")
        return False


async def _queue_for_review(batch_size: int):
    """In review mode: queue emails for Nico's approval instead of sending."""
    leads = await fetch_all(
        """SELECT c.id as client_id, c.email, c.business_name,
                  es.id as seq_id, es.subject, es.body
           FROM clients c
           JOIN email_sequences es ON es.client_id = c.id
           WHERE c.status = 'email_drafted' AND es.status = 'pending' AND es.step = 1
           LIMIT %s""",
        (batch_size,),
    )

    for lead in leads:
        from psycopg.types.json import Jsonb
        content = {"subject": lead.get("subject") or "", "body": lead.get("body") or ""}
        await execute(
            """INSERT INTO review_queue (item_type, client_id, content, status)
               VALUES ('email_draft', %s, %s, 'pending_review')""",
            (lead["client_id"], Jsonb(content)),
        )
        await transition_lead(lead["client_id"], "email_queued")

    if leads:
        await emit_event("review_needed", {"type": "email_drafts", "count": len(leads)})
        logger.info(f"Queued {len(leads)} emails for review")


async def sync_campaign_analytics():
    """
    Pull analytics from Instantly and update our outreach_metrics.
    Called periodically to track opens, replies, bounces.
    """
    campaign_id = await get_config("instantly_campaign_id", "")
    if not campaign_id:
        return

    try:
        from tools.instantly_client import InstantlyClient
        client = InstantlyClient()
        analytics = await client.get_campaign_analytics(campaign_id)
        await client.close()

        if isinstance(analytics, dict):
            from datetime import date
            # outreach_metrics table uses (date, domain, campaign) as unique key
            domain = analytics.get("domain", "perseus")
            await execute(
                """INSERT INTO outreach_metrics (date, domain, campaign, emails_sent, opens, replies)
                   VALUES (CURRENT_DATE, %s, %s, %s, %s, %s)
                   ON CONFLICT (date, domain, campaign) DO UPDATE SET
                   emails_sent = EXCLUDED.emails_sent,
                   opens = EXCLUDED.opens,
                   replies = EXCLUDED.replies""",
                (
                    domain,
                    campaign_id,
                    analytics.get("sent", 0),
                    analytics.get("opens", 0),
                    analytics.get("replies", 0),
                ),
            )
    except Exception as e:
        logger.debug(f"Analytics sync failed (non-critical): {e}")
