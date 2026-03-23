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
import re

from shared.db import (
    emit_event,
    execute,
    fetch_all,
    fetch_one,
    get_config,
    set_config,
    transaction,
)
from shared.pipeline_alerts import emit_pipeline_error
from titan.training import collect_training_example

logger = logging.getLogger("perseus.titan.email_send")

# Campaign name prefix — one active campaign at a time
CAMPAIGN_PREFIX = "perseus-outreach"
_SIMULATION_SCORE_THRESHOLD = 70

_SPAM_REASONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"ACT NOW", re.IGNORECASE), "urgency language"),
    (re.compile(r"LIMITED TIME", re.IGNORECASE), "urgency language"),
    (re.compile(r"FREE FREE", re.IGNORECASE), "repeated free"),
    (re.compile(r"CLICK HERE", re.IGNORECASE), "click here spam trigger"),
    (re.compile(r"BUY NOW", re.IGNORECASE), "buy now spam trigger"),
    (re.compile(r"\$\$\$"), "money symbols"),
    (re.compile(r"!!!"), "excessive punctuation"),
]

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}
_OUTLOOK_DOMAINS = {"outlook.com", "hotmail.com", "live.com", "msn.com"}
_PROVIDER_DAILY_CAPS = {
    "gmail": 100,
    "outlook": 100,
    "other": 50,
}
_HARD_BOUNCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"mailbox unavailable",
        r"user unknown",
        r"recipient rejected",
        r"does not exist",
        r"no such user",
        r"invalid recipient",
        r"5\.1\.1",
    )
]
_SOFT_BOUNCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"mailbox full",
        r"temporar",
        r"timed out",
        r"greylist",
        r"rate limit",
        r"try again later",
        r"4\.\d\.\d",
    )
]


def _simulate_email_micro_review(lead: dict) -> dict:
    """Score a draft from a few local persona perspectives before sending."""
    subject = lead.get("subject", "") or ""
    body = lead.get("body", "") or ""
    combined = f"{subject} {body}".strip()
    words = body.split()

    issues: list[str] = []
    if len(subject) > 55:
        issues.append("subject too long")
    if len(words) > 150:
        issues.append("body too long")
    if body.count("?") > 2:
        issues.append("too many questions")
    if len([word for word in words if word.isupper() and len(word) >= 4]) >= 3:
        issues.append("too much all-caps")

    spam_hits = []
    for pattern, reason in _SPAM_REASONS:
        if pattern.search(combined):
            spam_hits.append(reason)
            issues.append(reason)

    personas = [
        _score_skeptical_owner(lead, body, spam_hits, len(words), len(subject)),
        _score_time_starved_owner(subject, body, len(words), spam_hits),
        _score_deliverability_guard(combined, spam_hits),
    ]

    risk_score = min(persona["score"] for persona in personas)
    if issues or risk_score < _SIMULATION_SCORE_THRESHOLD:
        status = "flagged"
    else:
        status = "passed"

    if status == "flagged":
        summary = " | ".join(
            [
                "flagged by local micro-simulation",
                ", ".join(issues) if issues else "low confidence from persona review",
            ]
        )
    else:
        summary = "passed local micro-simulation across skeptical owner, time-starved owner, and deliverability guard"

    return {
        "status": status,
        "score": risk_score,
        "summary": summary,
        "personas": personas,
    }


def _score_skeptical_owner(
    lead: dict,
    body: str,
    spam_hits: list[str],
    word_count: int,
    subject_length: int,
) -> dict:
    score = 92
    if not lead.get("business_name"):
        score -= 8
    if "reply" not in body.lower():
        score -= 12
    if word_count > 90:
        score -= 10
    if subject_length > 45:
        score -= 5
    if spam_hits:
        score -= 35

    if score >= 80:
        concern = "looks credible"
    elif spam_hits:
        concern = "sounds promotional"
    else:
        concern = "could use a sharper value proposition"

    return {"persona": "skeptical_owner", "score": max(0, score), "concern": concern}


def _score_time_starved_owner(
    subject: str,
    body: str,
    word_count: int,
    spam_hits: list[str],
) -> dict:
    score = 94
    if word_count > 70:
        score -= 12
    if len(subject) > 45:
        score -= 8
    if body.count("!") > 1:
        score -= 10
    if spam_hits:
        score -= 25

    if score >= 80:
        concern = "easy to skim"
    elif spam_hits:
        concern = "feels noisy"
    else:
        concern = "could be tighter"

    return {"persona": "time_starved_owner", "score": max(0, score), "concern": concern}


def _score_deliverability_guard(combined: str, spam_hits: list[str]) -> dict:
    score = 96
    if spam_hits:
        score -= 45
    if re.search(r"\b[A-Z]{5,}\b", combined):
        score -= 10
    if "!" in combined:
        score -= 8

    if score >= 85:
        concern = "low spam risk"
    elif spam_hits:
        concern = "deliverability risk"
    else:
        concern = "watch punctuation"

    return {"persona": "deliverability_guard", "score": max(0, score), "concern": concern}


def _jsonb_value(value):
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return value

    return Jsonb(value)


async def _persist_micro_simulation(seq_id: int, simulation: dict):
    """Store the local review result on the draft row before sending."""
    await execute(
        """UPDATE email_sequences
           SET simulation_status = %s,
               simulation_score = %s,
               simulation_summary = %s,
               simulation_personas = %s,
               simulation_checked_at = NOW()
           WHERE id = %s""",
        (
            simulation["status"],
            simulation["score"],
            simulation["summary"],
            _jsonb_value(simulation["personas"]),
            seq_id,
        ),
    )


async def send_emails(batch_size: int = 50):
    """Add queued emails to Instantly campaign. Respects review mode and deliverability limits."""
    # Check review mode
    review_mode = await get_config("review_mode", True)
    if review_mode:
        await _queue_for_review(batch_size)
        return

    # Respect daily send budget from deliverability monitor
    try:
        from titan.deliverability import get_send_budget_today
        budget_remaining = await get_send_budget_today()
        if budget_remaining <= 0:
            logger.info("Daily send budget exhausted — skipping email_send this cycle")
            return
        batch_size = min(batch_size, budget_remaining)
    except ImportError:
        pass

    # Get emails ready to send
    leads = await fetch_all(
        """SELECT c.id as client_id, c.email, c.business_name,
                  c.contact_name, c.industry, c.city, c.country,
                  c.status as client_status,
                  es.id as seq_id, es.step, es.subject, es.body
           FROM clients c
           JOIN email_sequences es ON es.client_id = c.id
           WHERE c.status IN ('email_drafted', 'followed_up')
             AND es.status = 'pending'
           ORDER BY c.lead_score DESC LIMIT %s""",
        (max(batch_size * 4, batch_size),),
    )

    if not leads:
        return

    sent_today_by_provider = await _get_sent_today_by_provider()
    leads = _apply_provider_bucketing(leads, batch_size, sent_today_by_provider)
    if not leads:
        logger.info("Provider throttles exhausted today's safe recipient mix — skipping email_send")
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
            await emit_pipeline_error("email_send", e, client_id=lead["client_id"])

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

        if new_id:
            await set_config("instantly_campaign_id", new_id)
            logger.info(f"Created Instantly campaign: {name} ({new_id})")

            # Activate the campaign so Instantly starts sending
            await client.activate_campaign(new_id)

        await client.close()

        return new_id

    except ImportError:
        logger.warning("Instantly client not available")
        return ""
    except Exception as e:
        logger.error(f"Failed to get/create campaign: {e}")
        return ""


async def _add_lead_to_campaign(campaign_id: str, lead: dict) -> bool:
    """Add a single lead to the Instantly campaign via the compliance gate."""
    from titan.compliance import send_to_instantly

    simulation = _simulate_email_micro_review(lead)
    await _persist_micro_simulation(lead["seq_id"], simulation)
    if simulation["status"] == "flagged":
        logger.warning(
            "Simulation flagged lead %s (seq %s) before send: %s",
            lead["client_id"],
            lead["seq_id"],
            simulation["summary"],
        )
        return False

    success = await send_to_instantly(
        campaign_id=campaign_id,
        client_id=lead["client_id"],
        email=lead["email"],
        subject=lead.get("subject", ""),
        body=lead.get("body", ""),
        seq_id=lead["seq_id"],
        first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
        company_name=lead.get("business_name", ""),
        industry=lead.get("industry", ""),
        city=lead.get("city", ""),
        country=lead.get("country", ""),
    )

    if not success:
        return False

    # Update our tracking atomically so a crash can't partially advance state.
    async with transaction() as conn:
        await conn.execute(
            "UPDATE email_sequences SET status = 'sent', sent_at = NOW() WHERE id = %s",
            (lead["seq_id"],),
        )
        if lead.get("step", 1) == 1:
            await conn.execute(
                """UPDATE clients
                   SET status = 'email_sent', last_contact_at = NOW(), updated_at = NOW()
                   WHERE id = %s""",
                (lead["client_id"],),
            )
        else:
            await conn.execute(
                "UPDATE clients SET last_contact_at = NOW(), updated_at = NOW() WHERE id = %s",
                (lead["client_id"],),
            )

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


async def _queue_for_review(batch_size: int):
    """In review mode: queue emails for Nico's approval instead of sending."""
    from psycopg.types.json import Jsonb

    from titan.state_machine import transition_lead

    leads = await fetch_all(
        """SELECT c.id as client_id, c.email, c.business_name,
                  c.status as client_status,
                  es.id as seq_id, es.step, es.subject, es.body
           FROM clients c
           JOIN email_sequences es ON es.client_id = c.id
           WHERE c.status IN ('email_drafted', 'followed_up')
             AND es.status = 'pending'
           LIMIT %s""",
        (batch_size,),
    )

    for lead in leads:
        content = {
            "seq_id": lead["seq_id"],
            "step": lead.get("step", 1),
            "subject": lead.get("subject") or "",
            "body": lead.get("body") or "",
        }
        await execute(
            """INSERT INTO review_queue (item_type, client_id, content, status)
               VALUES ('email_draft', %s, %s, 'pending_review')""",
            (lead["client_id"], Jsonb(content)),
        )
        if lead.get("step", 1) == 1:
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

        if isinstance(analytics, dict):
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
        await _sync_sequence_engagement(client, campaign_id)
        await client.close()
    except Exception as e:
        logger.debug(f"Analytics sync failed (non-critical): {e}")


def _recipient_provider(email: str) -> str:
    domain = (email or "").strip().lower().split("@")[-1]
    if domain in _GMAIL_DOMAINS:
        return "gmail"
    if domain in _OUTLOOK_DOMAINS:
        return "outlook"
    return "other"


async def _get_sent_today_by_provider() -> dict[str, int]:
    row = await fetch_one(
        """SELECT
               COALESCE(SUM(CASE
                   WHEN LOWER(SPLIT_PART(c.email, '@', 2)) IN ('gmail.com', 'googlemail.com') THEN 1
                   ELSE 0
               END), 0) AS gmail,
               COALESCE(SUM(CASE
                   WHEN LOWER(SPLIT_PART(c.email, '@', 2)) IN ('outlook.com', 'hotmail.com', 'live.com', 'msn.com') THEN 1
                   ELSE 0
               END), 0) AS outlook,
               COALESCE(SUM(CASE
                   WHEN LOWER(SPLIT_PART(c.email, '@', 2)) NOT IN (
                       'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com'
                   ) THEN 1
                   ELSE 0
               END), 0) AS other
           FROM email_sequences es
           JOIN clients c ON c.id = es.client_id
           WHERE es.sent_at::date = CURRENT_DATE""",
    ) or {}
    return {
        "gmail": int(row.get("gmail", 0) or 0),
        "outlook": int(row.get("outlook", 0) or 0),
        "other": int(row.get("other", 0) or 0),
    }


def _apply_provider_bucketing(
    leads: list[dict],
    batch_size: int,
    sent_today_by_provider: dict[str, int] | None = None,
) -> list[dict]:
    counts = {
        provider: int((sent_today_by_provider or {}).get(provider, 0) or 0)
        for provider in _PROVIDER_DAILY_CAPS
    }
    selected: list[dict] = []

    for lead in leads:
        if len(selected) >= batch_size:
            break
        provider = _recipient_provider(lead.get("email", ""))
        cap = _PROVIDER_DAILY_CAPS.get(provider, _PROVIDER_DAILY_CAPS["other"])
        if counts.get(provider, 0) >= cap:
            continue
        counts[provider] = counts.get(provider, 0) + 1
        selected.append(lead)

    return selected


def _classify_bounce_reason(reason: str) -> str:
    normalized = (reason or "").strip()
    if not normalized:
        return ""
    for pattern in _HARD_BOUNCE_PATTERNS:
        if pattern.search(normalized):
            return "hard"
    for pattern in _SOFT_BOUNCE_PATTERNS:
        if pattern.search(normalized):
            return "soft"
    return ""


async def _sync_sequence_engagement(client, campaign_id: str):
    leads = await client.list_leads(campaign_id=campaign_id, limit=500)
    if not isinstance(leads, list):
        return

    for lead in leads:
        email = (lead.get("email") or "").strip().lower()
        if not email:
            continue

        open_count = int(lead.get("email_open_count") or lead.get("open_count") or lead.get("opens") or 0)
        reply_count = int(lead.get("email_reply_count") or lead.get("reply_count") or lead.get("replies") or 0)
        bounce_reason = lead.get("bounce_reason") or lead.get("last_error") or ""
        bounce_type = _classify_bounce_reason(bounce_reason)

        if open_count > 0:
            await execute(
                """UPDATE email_sequences
                   SET opened_at = COALESCE(opened_at, NOW()),
                       status = CASE WHEN status = 'sent' THEN 'opened' ELSE status END
                   WHERE id = (
                       SELECT es.id
                       FROM email_sequences es
                       JOIN clients c ON c.id = es.client_id
                       WHERE LOWER(c.email) = %s
                         AND es.sent_at IS NOT NULL
                       ORDER BY es.sent_at DESC NULLS LAST, es.id DESC
                       LIMIT 1
                   )""",
                (email,),
            )

        if reply_count > 0:
            await execute(
                """UPDATE email_sequences
                   SET opened_at = COALESCE(opened_at, NOW()),
                       replied_at = COALESCE(replied_at, NOW()),
                       status = 'replied'
                   WHERE id = (
                       SELECT es.id
                       FROM email_sequences es
                       JOIN clients c ON c.id = es.client_id
                       WHERE LOWER(c.email) = %s
                         AND es.sent_at IS NOT NULL
                       ORDER BY es.sent_at DESC NULLS LAST, es.id DESC
                       LIMIT 1
                   )""",
                (email,),
            )

        if bounce_type == "hard":
            await execute(
                """UPDATE email_sequences
                   SET status = 'bounced'
                   WHERE id = (
                       SELECT es.id
                       FROM email_sequences es
                       JOIN clients c ON c.id = es.client_id
                       WHERE LOWER(c.email) = %s
                         AND es.sent_at IS NOT NULL
                       ORDER BY es.sent_at DESC NULLS LAST, es.id DESC
                       LIMIT 1
                   )""",
                (email,),
            )
            await execute(
                """UPDATE clients
                   SET status = 'unsubscribed', updated_at = NOW()
                   WHERE LOWER(email) = %s""",
                (email,),
            )
        elif bounce_type == "soft":
            await emit_event(
                "soft_bounce_detected",
                {"campaign_id": campaign_id, "email": email, "bounce_reason": bounce_reason},
            )
