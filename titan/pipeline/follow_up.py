"""
Stage 5: Follow-Up
AI decides persistence per lead. Handle replies, multi-step sequences.
"""

import json
import logging
from datetime import datetime, timedelta

from shared.db import fetch_all, fetch_one, execute, emit_event
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead
from titan.memory import get_relevant_learnings
from titan.training import collect_email_outcome, collect_training_example

logger = logging.getLogger("perseus.titan.follow_up")


async def process_follow_ups():
    """Check for replies and send follow-ups where needed."""
    await _check_replies()
    await _send_follow_ups()


async def _check_replies():
    """Check Instantly.ai for new unread replies and classify them."""
    replies = []
    fetch_client = None
    try:
        from tools.instantly_client import InstantlyClient
        from shared.db import get_config
        fetch_client = InstantlyClient()

        campaign_id = await get_config("instantly_campaign_id", "")
        # Get unread emails from Instantly inbox
        replies = await fetch_client.list_emails(
            campaign_id=campaign_id,
            is_unread=True,
            limit=50,
        )
        if not isinstance(replies, list):
            replies = replies.get("data", []) if isinstance(replies, dict) else []
    except Exception as e:
        logger.warning(f"Could not check replies: {e}")
        if fetch_client:
            try:
                await fetch_client.close()
            except Exception as close_exc:
                logger.warning(f"Could not close Instantly replies client cleanly: {close_exc}")
        return
    finally:
        if fetch_client:
            try:
                await fetch_client.close()
            except Exception as e:
                logger.warning(f"Could not close Instantly replies client cleanly: {e}")

    for reply in replies:
        try:
            processed = await _process_reply(reply)
        except Exception as e:
            reply_id = reply.get("id", "")
            lead_email = reply.get("lead", reply.get("from_email", reply.get("email", "")))
            logger.error(f"Reply processing failed for {lead_email or reply_id}: {e}")
            await emit_pipeline_error(
                "follow_up.reply_process",
                e,
                lead_email=lead_email,
                reply_id=reply_id,
            )
            continue

        email_id = reply.get("id", "")
        if processed and email_id:
            try:
                await _mark_reply_read(email_id)
            except Exception as e:
                logger.warning(f"Processed reply {email_id} but could not mark thread read: {e}")


async def _mark_reply_read(email_id: str):
    """Mark a processed reply as read using a fresh Instantly client."""
    from tools.instantly_client import InstantlyClient

    client = InstantlyClient()
    try:
        await client.mark_thread_read(email_id)
    finally:
        await client.close()


async def _process_reply(reply: dict) -> bool:
    """Process one Instantly reply and return True when it was handled successfully."""
    # Instantly returns lead email in the reply object
    email = reply.get("lead", reply.get("from_email", reply.get("email", "")))
    lead = await fetch_one("SELECT id, status FROM clients WHERE email = %s", (email,))
    if not lead:
        return False

    # Classify the reply using AI
    intent = await _classify_reply(reply.get("body", ""), reply.get("subject", ""))

    # Record training data from the reply outcome
    outcome_map = {
        "interested": "positive",
        "not_interested": "negative",
        "unsubscribe": "negative",
        "question": "positive",  # engagement is positive signal
        "out_of_office": "",      # no signal
    }
    outcome = outcome_map.get(intent, "")

    # Find the email sequence that triggered this reply
    email_seq = await fetch_one(
        """SELECT id FROM email_sequences
           WHERE client_id = %s AND status = 'sent'
           ORDER BY sent_at DESC LIMIT 1""",
        (lead["id"],),
    )
    if email_seq and outcome:
        await collect_email_outcome(email_seq["id"], outcome)

    # Record the reply classification as a training example
    await collect_training_example(
        example_type="reply_classify",
        input_text=f"Subject: {reply.get('subject', '')}\nBody: {reply.get('body', '')}",
        output_text=intent,
        outcome=outcome,
        metadata={"client_id": lead["id"]},
    )

    if intent == "interested":
        await transition_lead(lead["id"], "interested")
        await emit_event("lead_interested", {
            "client_id": lead["id"],
            "reply_preview": reply.get("body", "")[:200],
        })
        logger.info(f"Lead {lead['id']} replied with interest!")
    elif intent == "not_interested":
        await transition_lead(lead["id"], "lost")
    elif intent == "unsubscribe":
        await transition_lead(lead["id"], "unsubscribed")
    else:
        # Question or unclear — needs follow-up
        await transition_lead(lead["id"], "replied")

    return True


async def _classify_reply(body: str, subject: str) -> str:
    """Classify a reply into intent categories."""
    return await llm.classify(
        f"Subject: {subject}\nBody: {body}",
        ["interested", "not_interested", "question", "unsubscribe", "out_of_office"],
    )


async def _send_follow_ups():
    """Send follow-ups for leads that haven't responded."""
    # Get leads that need follow-up (sent but no reply, enough time passed)
    leads = await fetch_all(
        """SELECT c.id, c.business_name, c.email, c.follow_up_count,
                  c.last_contact_at, c.research_summary, c.language
           FROM clients c
           WHERE c.status IN ('email_sent', 'followed_up')
           AND c.last_contact_at < NOW() - INTERVAL '3 days'
           AND c.follow_up_count < 7
           ORDER BY c.lead_score DESC LIMIT 20"""
    )

    for lead in leads:
        try:
            # AI decides if we should follow up
            should_follow = await _should_follow_up(lead)
            if should_follow:
                await _compose_and_queue_follow_up(lead)
        except Exception as e:
            logger.error(f"Follow-up failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("follow_up", e, lead_id=lead["id"])


async def _should_follow_up(lead: dict) -> bool:
    """AI decides if a follow-up makes sense for this lead."""
    step = lead.get("follow_up_count", 0)
    if step >= 7:
        return False

    # First 3 follow-ups: always yes
    if step < 3:
        return True

    # After 3: ask AI
    prompt = f"""Should we send follow-up #{step + 1} to {lead['business_name']}?
They haven't replied to {step} previous emails.
Last contact: {lead.get('last_contact_at', 'unknown')}
Industry: {lead.get('research_summary', 'unknown')[:200]}

Answer YES or NO with one sentence reasoning."""

    result = await llm.route(prompt, max_tokens=50, temperature=0.3)  # 3B: yes/no gate
    return "yes" in result.lower()


async def _compose_and_queue_follow_up(lead: dict):
    """Compose and queue a follow-up email."""
    step = lead.get("follow_up_count", 0) + 1
    lang = lead.get("language", "en")

    # Get learnings about what follow-up approaches work
    learnings = await get_relevant_learnings(
        "follow-up emails, re-engagement, what gets replies on second/third contact"
    )

    prompt = f"""Write follow-up email #{step} for {lead['business_name']}.
Previous emails got no response. This is a cold outreach about building them a website.
Language: {'Spanish' if lang == 'es' else 'English'}

WHAT WE'VE LEARNED WORKS:
{learnings}

Key rules:
- Different angle from previous emails
- Even shorter than the first email (under 80 words)
- Reference something specific about their business
- Friendly, not pushy

Return JSON: {{"subject": "...", "body": "..."}}"""

    result = await llm.generate(prompt, model="fast-remote", temperature=0.8)  # Claude: client-facing follow-up
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        email_data = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Could not parse follow-up JSON for lead {lead['id']}")
        await emit_pipeline_error(
            "follow_up.compose_parse",
            ValueError("Could not parse follow-up JSON"),
            lead_id=lead["id"],
        )
        return

    # Store follow-up and only advance the lead if the sequence row exists.
    queued_email = await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status)
           VALUES (%s, %s, %s, %s, 'pending') RETURNING id""",
        (lead["id"], step, email_data.get("subject", ""), email_data.get("body", "")),
    )
    if not queued_email or not queued_email.get("id"):
        logger.error(f"Could not persist follow-up #{step} for lead {lead['id']}")
        await emit_pipeline_error(
            "follow_up.queue_insert",
            ValueError("Could not persist follow-up email sequence"),
            lead_id=lead["id"],
            step=step,
        )
        return

    # Update lead
    await execute(
        "UPDATE clients SET follow_up_count = %s WHERE id = %s",
        (step, lead["id"]),
    )
    await transition_lead(lead["id"], "followed_up")
    logger.info(f"Queued follow-up #{step} for lead {lead['id']}")
