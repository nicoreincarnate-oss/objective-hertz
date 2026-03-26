"""
Stage 5: Follow-Up
AI decides persistence per lead. Handle replies, multi-step sequences.
"""

import json
import logging
from datetime import UTC

from shared.db import emit_event, execute, fetch_all, fetch_one, get_config
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from titan.memory import attribute_reply_cause, format_rules_for_prompt, get_relevant_learnings
from titan.state_machine import transition_lead
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
        from shared.db import get_config
        from tools.instantly_client import InstantlyClient
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
        return
    finally:
        if fetch_client:
            try:
                await fetch_client.close()
            except Exception as close_exc:
                logger.warning(f"Could not close Instantly replies client cleanly: {close_exc}")

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
    if not email:
        logger.warning("Reply missing lead email — skipping: reply_id=%s", reply.get("id", "?"))
        return False
    lead = await fetch_one("SELECT id, status FROM clients WHERE LOWER(email) = LOWER(%s)", (email,))
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
    # Match 'queued', 'sent', or 'opened' — the sequence may have been
    # queued but not yet confirmed sent, or analytics may have already
    # advanced it to 'opened'. We still need attribution in all cases.
    email_seq = await fetch_one(
        """SELECT id FROM email_sequences
           WHERE client_id = %s AND status IN ('queued', 'sent', 'opened')
           ORDER BY created_at DESC LIMIT 1""",
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

    # Causal credit assignment (2.2): analyze which sentence caused the reply
    if outcome in ("positive", "negative") and email_seq:
        try:
            orig_email = await fetch_one(
                "SELECT subject, body FROM email_sequences WHERE id = %s",
                (email_seq["id"],),
            )
            if orig_email and orig_email.get("body"):
                original_text = f"Subject: {orig_email.get('subject', '')}\n\n{orig_email['body']}"
                await attribute_reply_cause(
                    original_email=original_text,
                    reply_body=reply.get("body", "")[:1000],
                    outcome=outcome,
                    client_id=lead["id"],
                )
        except Exception as e:
            logger.debug(f"Causal attribution skipped for lead {lead['id']}: {e}")

    return True


# Multilingual unsubscribe keywords — deterministic first-pass check.
# Missing an unsubscribe request is a legal violation; a 3B model can't be trusted
# to catch "darse de baja" or "désabonnez" reliably.  This list is cheap and safe.
_UNSUB_KEYWORDS: set[str] = {
    # English
    "unsubscribe", "opt out", "opt-out", "remove me", "stop emailing",
    "stop mailing", "take me off", "do not contact", "don't contact",
    "no more emails", "remove from list", "cancel subscription",
    # Spanish
    "darse de baja", "darme de baja", "cancelar suscripción", "cancelar suscripcion",
    "no me contacten", "no me escriban", "eliminar de la lista", "dejar de recibir",
    "no quiero recibir", "baja de la lista",
    # French
    "désabonner", "desabonner", "se désinscrire", "se desinscrire",
    "ne plus recevoir", "supprimer de la liste",
    # German
    "abmelden", "abbestellen", "austragen", "keine e-mails mehr",
    "nicht mehr kontaktieren",
    # Portuguese
    "cancelar inscrição", "cancelar inscricao", "descadastrar",
    "não quero receber", "nao quero receber", "remover da lista",
    # Italian
    "annullare l'iscrizione", "cancellare iscrizione", "non contattarmi",
    "rimuovi dalla lista",
    # Dutch
    "uitschrijven", "afmelden", "verwijder mij",
    # Japanese (romaji + common)
    "配信停止", "配信解除", "登録解除",
    # Korean
    "수신거부", "구독취소",
    # Chinese
    "退订", "取消订阅",
}


def _is_unsub_by_keyword(text: str) -> bool:
    """Fast deterministic unsubscribe detection across languages."""
    lowered = text.lower()
    return any(kw in lowered for kw in _UNSUB_KEYWORDS)


async def _classify_reply(body: str, subject: str) -> str:
    """Classify a reply into intent categories.

    Layer 1: deterministic multilingual keyword scan (free, instant, no false negatives).
    Layer 2: LLM classification for everything else.
    """
    combined = f"{subject} {body}"
    if _is_unsub_by_keyword(combined):
        return "unsubscribe"

    return await llm.classify(
        f"Subject: {subject}\nBody: {body}",
        ["interested", "not_interested", "question", "unsubscribe", "out_of_office"],
    )


# Default follow-up delays in days, indexed by follow_up_count (0 = first follow-up).
# Configurable via system_config key "follow_up_timing_days".
DEFAULT_FOLLOW_UP_TIMING_DAYS = [3, 3, 7, 14, 21, 28, 35]

# Per-step prompt angles to vary the approach across the sequence.
STEP_ANGLES = [
    "value reminder — restate the core benefit",
    "social proof — mention how many businesses you've helped",
    "curiosity — ask a question about their business",
    "scarcity — mention limited availability this month",
    "case study — share a quick success story from their industry",
    "direct ask — simple yes/no question",
    "breakup — friendly last-chance message",
]


async def _get_follow_up_delay_days(step: int) -> int:
    """Get the delay in days before sending follow-up at this step."""
    timing = await get_config("follow_up_timing_days", None)
    if timing and isinstance(timing, list) and step < len(timing):
        return int(timing[step])
    if step < len(DEFAULT_FOLLOW_UP_TIMING_DAYS):
        return DEFAULT_FOLLOW_UP_TIMING_DAYS[step]
    return 35  # fallback for steps beyond the configured list


async def _send_follow_ups():
    """Send follow-ups for leads that haven't responded."""
    max_steps = len(DEFAULT_FOLLOW_UP_TIMING_DAYS)
    timing_config = await get_config("follow_up_timing_days", None)
    if timing_config and isinstance(timing_config, list):
        max_steps = len(timing_config)

    # Fetch all eligible leads; we filter by per-step timing in Python
    # since each lead may have a different follow_up_count and thus a different delay.
    leads = await fetch_all(
        """SELECT c.id, c.business_name, c.email, c.follow_up_count,
                  c.last_contact_at, c.research_summary, c.language, c.lead_score
           FROM clients c
           WHERE c.status IN ('email_sent', 'email_queued', 'followed_up')
           AND c.follow_up_count < %s
           ORDER BY c.lead_score DESC LIMIT 20""",
        (max_steps,),
    )

    from datetime import datetime, timedelta
    now = datetime.now(UTC)
    filtered = []
    for lead in leads:
        step = lead.get("follow_up_count", 0)
        delay_days = await _get_follow_up_delay_days(step)
        last_contact = lead.get("last_contact_at")
        if last_contact is None:
            filtered.append(lead)
            continue
        if isinstance(last_contact, str):
            try:
                last_contact = datetime.fromisoformat(last_contact)
            except ValueError:
                filtered.append(lead)
                continue
        if last_contact.tzinfo is None:
            last_contact = last_contact.replace(tzinfo=UTC)
        if now - last_contact >= timedelta(days=delay_days):
            filtered.append(lead)
    leads = filtered

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

    result = await llm.generate(prompt, model="fast", max_tokens=50, temperature=0.3)
    return "yes" in result.lower()


async def _compose_and_queue_follow_up(lead: dict):
    """Compose and queue a follow-up email."""
    step = lead.get("follow_up_count", 0) + 1
    lang = lead.get("language", "en")

    # Get learnings about what follow-up approaches work (scoped to this client + system techniques)
    learnings = await get_relevant_learnings(
        "follow-up emails, re-engagement, what gets replies on second/third contact",
        client_id=lead.get("id"),
        query_type="email_compose",
    )

    # Get proven rules (data-backed constraints)
    rules_block = await format_rules_for_prompt(["email_performance", "copywriting", "timing"])

    # Pick a step-specific angle for variety across the sequence
    angle_idx = min(step - 1, len(STEP_ANGLES) - 1)
    angle = STEP_ANGLES[angle_idx] if angle_idx >= 0 else "value reminder"

    prompt = f"""Write follow-up email #{step} for {lead['business_name']}.
Previous emails got no response. This is a cold outreach about building them a website.
Language: {'Spanish' if lang == 'es' else 'English'}

APPROACH FOR THIS EMAIL: {angle}

WHAT WE'VE LEARNED WORKS:
{learnings}

{rules_block}

Key rules:
- Use the "{angle}" approach above
- Different angle from previous emails
- Even shorter than the first email (under 80 words)
- Reference something specific about their business
- Friendly, not pushy

Return JSON: {{"subject": "...", "body": "..."}}"""

    result = await llm.generate(prompt, model="fast", temperature=0.8)
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

    # Store follow-up — ON CONFLICT prevents duplicate step entries from races
    queued_email = await fetch_one(
        """INSERT INTO email_sequences (client_id, step, subject, body, status)
           VALUES (%s, %s, %s, %s, 'pending')
           ON CONFLICT (client_id, step) DO NOTHING
           RETURNING id""",
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

    # Update lead — increment follow_up_count and record last_contact_at,
    # but do NOT transition to 'followed_up'. The email is only drafted and
    # queued at this point. The actual transition to 'followed_up' happens
    # in email_send.py when the email is accepted by Instantly.
    await execute(
        "UPDATE clients SET follow_up_count = %s, updated_at = NOW() WHERE id = %s",
        (step, lead["id"]),
    )
    logger.info(f"Queued follow-up #{step} for lead {lead['id']} (pending send)")
