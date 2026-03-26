"""
Stage 6-7: Close Deal + Demo Site
When a lead is interested: build a free demo site, handle sales conversation, close.
Uses Claude Sonnet for high-quality proposals.
"""

import json
import logging

from shared.db import (
    emit_event,
    execute,
    fetch_all,
    get_config,
    increment_config_int,
    set_config,
)
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from titan.memory import get_relevant_learnings
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.close")


def _site_build_fail_open_when_qa_unavailable() -> bool:
    try:
        from shared.config import config

        return bool(getattr(getattr(config, "site_build", object()), "fail_open_when_qa_unavailable", False))
    except Exception:
        return False


async def process_interested_leads():
    """Handle interested leads: demo site → proposal → close."""
    # Get interested leads that need a demo site
    leads_need_demo = await fetch_all(
        """SELECT id, business_name, contact_name, email, industry,
                  research_summary, research_facts, language, country, city
           FROM clients WHERE status = 'interested'
           ORDER BY lead_score DESC LIMIT 5"""
    )

    for lead in leads_need_demo:
        try:
            await _build_demo_and_propose(lead)
        except Exception as e:
            logger.error(f"Demo/proposal failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("close_deal", e, lead_id=lead["id"])


async def _build_demo_and_propose(lead: dict):
    """Build a free demo site for the lead and send a proposal."""
    lead_id = lead["id"]

    # Check if we need Nico's approval (first 10 sales)
    sales_completed = await get_config("sales_completed", 0)
    sales_threshold = await get_config("sales_before_autonomy", 10)
    needs_approval = sales_completed < sales_threshold

    # Build a quick demo site
    demo_url = await _build_demo_site(lead)

    if demo_url:
        # QA the demo site before using it in a proposal
        from shared.comms import request_task_result
        qa_result = await request_task_result(
            "verify_demo_site",
            payload={
                "url": demo_url,
                "business_name": lead["business_name"],
                "client_id": lead_id,
                "site_type": "demo",
            },
            timeout_seconds=45,
        )
        if qa_result and qa_result.get("ok") and qa_result.get("result", {}).get("passed"):
            await execute(
                "UPDATE clients SET demo_site_url = %s WHERE id = %s",
                (demo_url, lead_id),
            )
            await transition_lead(lead_id, "demo_built")
        elif qa_result and qa_result.get("ok"):
            # QA ran but failed — block the proposal
            reason = qa_result.get("result", {}).get("reason", "qa_failed")
            logger.warning(f"Demo site QA failed for lead {lead_id}: {reason}")
            await emit_event("proposal_blocked", {
                "client_id": lead_id,
                "business_name": lead["business_name"],
                "reason": f"demo_qa_failed: {reason}",
                "demo_url": demo_url,
            })
            return
        else:
            if _site_build_fail_open_when_qa_unavailable():
                logger.info(f"Demo QA unavailable for lead {lead_id}, accepting demo due to fail-open config")
                await execute(
                    "UPDATE clients SET demo_site_url = %s WHERE id = %s",
                    (demo_url, lead_id),
                )
                await transition_lead(lead_id, "demo_built")
            else:
                logger.warning(f"Demo QA unavailable for lead {lead_id}, blocking proposal")
                await emit_event("proposal_blocked", {
                    "client_id": lead_id,
                    "business_name": lead["business_name"],
                    "reason": "demo_qa_unavailable",
                    "demo_url": demo_url,
                })
                return
    else:
        logger.warning(f"Could not build demo for lead {lead_id}, blocking proposal until demo exists")
        await emit_event("proposal_blocked", {
            "client_id": lead_id,
            "business_name": lead["business_name"],
            "reason": "demo_build_failed",
        })
        return

    # Generate proposal using Claude Sonnet (high quality)
    proposal = await _generate_proposal(lead, demo_url)

    # 4.2: Red-team high-value proposals (4 agents: writer→attacker→optimizer→tone checker)
    if proposal.get("body") and float(lead.get("lead_score", 0) or 0) >= 70:
        try:
            proposal = await _red_team_proposal(proposal, lead)
        except Exception as e:
            logger.debug(f"Red-team skipped for lead {lead_id} (non-critical): {e}")

    if needs_approval:
        # Queue for Nico's review
        await execute(
            """INSERT INTO review_queue (item_type, client_id, content, status)
               VALUES ('proposal', %s, %s, 'pending_review')""",
            (lead_id, json.dumps(proposal, default=str)),
        )
        await emit_event("review_needed", {
            "type": "proposal",
            "client_id": lead_id,
            "business_name": lead["business_name"],
        })
        logger.info(f"Proposal for {lead['business_name']} queued for Nico's review")
    else:
        # Send proposal autonomously — only transition if actually sent
        sent = await _send_proposal(lead, proposal, demo_url)
        if sent:
            await transition_lead(lead_id, "proposal_sent")
            logger.info(f"Proposal sent to {lead['business_name']} (autonomous)")
        else:
            logger.error(f"Proposal send FAILED for {lead['business_name']} — lead stays at current state")
            await emit_event("proposal_send_failed", {
                "client_id": lead_id,
                "business_name": lead["business_name"],
            })


async def _build_demo_site(lead: dict) -> str:
    """Build a demo landing page using ClawdBot's build_demo_site capability via A2A."""
    from shared.comms import call_agent_capability
    result = await call_agent_capability(
        "clawdbot", "build_demo_site", {"lead": lead}, timeout=120,
    )
    if not result or result.get("status") == "error":
        raise RuntimeError(f"ClawdBot demo site build failed: {result}")
    return result.get("url", "")


async def _get_dynamic_price(lead: dict) -> dict:
    """
    Pick a price for this lead based on industry, region, and what's worked before.
    Tracks the offered price on the client row so we can measure conversion by price.
    """
    from shared.config import config as cfg

    base = cfg.pricing.website_5page  # default $299
    industry = (lead.get("industry") or "").lower()
    country = (lead.get("country") or "").lower()
    score = float(lead.get("lead_score", 50) or 50)

    # Check for pricing rules from titan_rules
    from titan.memory import format_rules_for_prompt
    pricing_rules = await format_rules_for_prompt(["pricing"])

    # Simple tiers: high-score leads in premium industries get a higher price
    premium_industries = {"dental", "dentist", "law", "legal", "medical", "clinic", "real estate"}
    budget_regions = {"mexico", "india", "philippines", "colombia", "brazil", "argentina"}

    price = base
    if any(kw in industry for kw in premium_industries) and score >= 70:
        price = min(base + 50, 399)  # premium tier
    elif any(kw in country for kw in budget_regions):
        price = max(base - 50, 199)  # regional discount

    # Record the offered price so we can track conversion by price point
    await execute(
        "UPDATE clients SET notes = COALESCE(notes, '') || %s WHERE id = %s",
        (f"\n[price_offered: ${price}]", lead["id"]),
    )

    return {
        "website_price": price,
        "hosting_price": cfg.pricing.hosting_monthly,
        "receptionist_price": cfg.pricing.receptionist_monthly,
        "pricing_rules": pricing_rules,
    }


async def _generate_proposal(lead: dict, demo_url: str = "") -> dict:
    """Generate a custom proposal using Claude Sonnet with dynamic pricing."""
    demo_mention = f"\nI already built a demo site for you: {demo_url}" if demo_url else ""

    # Get learnings about what closes deals (scoped to this client + system techniques)
    learnings = await get_relevant_learnings(
        "sales proposals, closing deals, pricing objections, what converts interested leads",
        client_id=lead.get("id"),
        query_type="proposal_generation",
    )

    # Dynamic pricing based on lead quality, industry, region
    pricing = await _get_dynamic_price(lead)

    prompt = f"""You are Titan's sales closer. Write a proposal email for:

WHAT WE'VE LEARNED ABOUT CLOSING:
{learnings}

Business: {lead['business_name']}
Contact: {lead.get('contact_name', 'there')}
Industry: {lead.get('industry', '')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Research: {lead.get('research_summary', '')}
Language: {'Spanish' if lead.get('language') == 'es' else 'English'}
{demo_mention}

{pricing.get('pricing_rules', '')}

PRICING:
- Professional 5-page website: ${pricing['website_price']}
- Includes: custom design, mobile responsive, SEO optimized, contact forms
- Hosting available: ${pricing['hosting_price']}/month
- Optional AI receptionist add-on: ${pricing['receptionist_price']}/month
- Timeline: delivered within 5-7 business days

Write a warm, professional proposal email. Under 200 words.
Include the demo site link if available.
Make it feel personal, not corporate.

Return JSON:
{{
    "subject": "...",
    "body": "...",
    "pricing_summary": "...",
    "estimated_close_probability": 0.5
}}"""

    result = await llm.generate(prompt, model="smart", temperature=0.6)
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        return {
            "subject": f"Website proposal for {lead['business_name']}",
            "body": f"Hi {lead.get('contact_name', 'there')}, I'd love to help {lead['business_name']} with a professional website.",
            "pricing_summary": "$299 for a 5-page professional website",
            "estimated_close_probability": 0.3,
        }


async def _red_team_proposal(proposal: dict, lead: dict) -> dict:
    """4-agent red-team pipeline: Writer→Attacker→Optimizer→Tone Checker.

    Cost: ~$0.03-0.06 per proposal (Sonnet×3 + Haiku×1).
    The $0.01 tone check prevents a $299 deal dying to robotic copy.
    """
    original_body = proposal.get("body", "")
    business = lead.get("business_name", "Business")

    # Agent 2 (Attacker): plays the skeptical prospect
    attack = await llm.generate(
        f"You are {business}'s owner. You're busy, skeptical, "
        f"and have been burned by web agencies before. Read this proposal and "
        f"explain exactly why you WON'T reply:\n\n{original_body}\n\n"
        f"Be specific: what feels generic? What's missing? What would make you hit delete?",
        model="smart", temperature=0.6,
    )

    # Agent 3 (Optimizer): synthesizes writer + attacker
    optimized = await llm.generate(
        f"Original proposal:\n{original_body}\n\n"
        f"Prospect's likely objections:\n{attack}\n\n"
        f"Rewrite the proposal to preemptively address every objection. "
        f"Keep it under 200 words. Make it impossible to ignore.\n\n"
        f"Return ONLY the rewritten email body, no JSON.",
        model="smart", temperature=0.4,
    )

    # Agent 4 (Tone Checker): anti-AI-voice gate
    tone_result = await llm.generate(
        f"Read this cold email proposal and flag problems:\n\n{optimized}\n\n"
        f"Check for:\n"
        f"1. AI-sounding phrases ('leverage', 'streamline', 'I'd love to', 'excited to')\n"
        f"2. Overly formal tone (no human talks like this in email)\n"
        f"3. Spam trigger words (guarantee, limited time, act now, exclusive)\n"
        f"4. Generic filler that could apply to any business\n"
        f"5. Sentences longer than 20 words\n\n"
        f"Return JSON: {{\"passes\": true, \"issues\": []}} or "
        f"{{\"passes\": false, \"issues\": [\"...\"], \"rewrite\": \"...\"}}",
        model="fast", temperature=0.2,
    )

    # Parse tone check
    final_body = optimized
    try:
        start = tone_result.find("{")
        end = tone_result.rfind("}") + 1
        tone_data = json.loads(tone_result[start:end])
        if not tone_data.get("passes") and tone_data.get("rewrite"):
            final_body = tone_data["rewrite"]
    except (json.JSONDecodeError, ValueError):
        pass  # Use optimizer output if tone check parse fails

    proposal["body"] = final_body
    proposal["red_teamed"] = True
    logger.info(f"Proposal red-teamed for {business} (4 agents)")
    return proposal


async def _send_proposal(lead: dict, proposal: dict, demo_url: str = "") -> bool:
    """Send the proposal email via Instantly campaign through compliance gate."""
    from titan.compliance import send_to_instantly

    campaign_id = await _get_or_create_proposals_campaign()
    if not campaign_id:
        logger.error("No Instantly campaign available for proposals")
        return False

    success = await send_to_instantly(
        campaign_id=campaign_id,
        client_id=lead["id"],
        email=lead["email"],
        subject=proposal.get("subject", ""),
        body=proposal.get("body", ""),
        message_type="proposal",
        first_name=(lead.get("contact_name") or "").strip().split()[0] if (lead.get("contact_name") or "").strip() else "",
        company_name=lead.get("business_name", ""),
    )

    if success:
        await execute(
            "UPDATE clients SET last_contact_at = NOW() WHERE id = %s",
            (lead["id"],),
        )

    return success


async def _get_or_create_proposals_campaign() -> str:
    """Reuse the dedicated proposals campaign, or create it once and persist the id."""
    campaign_id = await get_config("instantly_proposals_campaign_id", "")
    if campaign_id:
        return campaign_id

    from tools.instantly_client import InstantlyClient

    campaign_name = "perseus-proposals"
    client = InstantlyClient()
    created_new = False
    try:
        campaigns = await client.list_campaigns()
        if isinstance(campaigns, dict):
            campaigns = campaigns.get("data", [])
        if not isinstance(campaigns, list):
            campaigns = []

        existing = next(
            (
                campaign for campaign in campaigns
                if str(campaign.get("name", "")).strip() == campaign_name and campaign.get("id")
            ),
            None,
        )
        if existing:
            campaign_id = existing["id"]
        else:
            campaign = await client.create_campaign(campaign_name)
            campaign_id = campaign.get("id", "")
            created_new = bool(campaign_id)

        if not campaign_id:
            return ""

        await client.activate_campaign(campaign_id)
        try:
            await set_config("instantly_proposals_campaign_id", campaign_id)
        except Exception as e:
            logger.warning(
                "Could not persist instantly_proposals_campaign_id=%s after %s: %s",
                campaign_id,
                "creation" if created_new else "reuse",
                e,
            )
        return campaign_id
    except Exception as e:
        logger.error(f"Failed to get or create proposals campaign: {e}")
        return ""
    finally:
        await client.close()

async def mark_sale_closed(client_id: int):
    """Called when a sale is confirmed (payment received or verbal yes)."""
    await transition_lead(client_id, "closed")

    # Record all emails in this client's sequence as positive training examples
    emails = await fetch_all(
        """SELECT id FROM email_sequences
           WHERE client_id = %s AND status = 'sent'""",
        (client_id,),
    )
    from titan.training import collect_email_outcome as _collect
    for email in emails:
        await _collect(email["id"], "positive")

    # Increment sales counter atomically so concurrent closes don't lose updates.
    sales = await increment_config_int("sales_completed", 1, default=0)

    # Check if we should disable review mode
    threshold = await get_config("sales_before_autonomy", 10)
    if sales >= threshold:
        await set_config("review_mode", False)
        await emit_event("autonomy_unlocked", {
            "sales_completed": sales,
            "message": "Review mode disabled — Titan is now fully autonomous!",
        })

    await emit_event("deal_closed", {"client_id": client_id, "sale_number": sales})
    logger.info(f"Sale #{sales} closed for client {client_id}!")
