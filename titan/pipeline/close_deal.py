"""
Stage 6-7: Close Deal + Demo Site
When a lead is interested: build a free demo site, handle sales conversation, close.
Uses Claude Sonnet for high-quality proposals.
"""

import json
import logging

from shared.db import fetch_all, fetch_one, execute, emit_event, get_config, set_config, increment_config_int
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead
from titan.memory import get_relevant_learnings
from titan.training import collect_training_example

logger = logging.getLogger("perseus.titan.close")


async def process_interested_leads():
    """Handle interested leads: demo site → proposal → close."""
    # Get interested leads that need a demo site
    leads_need_demo = await fetch_all(
        """SELECT id, business_name, contact_name, email, industry,
                  research_summary, language, country, city
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
    from shared.risk_gate import gate_or_queue, check_autonomy

    lead_id = lead["id"]

    # Risk gate: demo build is low-risk
    demo_allowed = await check_autonomy("demo_build")
    if not demo_allowed["allowed"]:
        logger.info(f"Demo build gated for lead {lead_id}: {demo_allowed['reason']}")
        await emit_event("review_needed", {
            "type": "demo_site",
            "client_id": lead_id,
            "business_name": lead["business_name"],
            "reason": demo_allowed["reason"],
        })
        return

    # Build a quick demo site
    demo_url = await _build_demo_site(lead)

    if demo_url:
        await execute(
            "UPDATE clients SET demo_site_url = %s WHERE id = %s",
            (demo_url, lead_id),
        )
        await transition_lead(lead_id, "demo_built")
    else:
        logger.warning(f"Could not build demo for lead {lead_id}, blocking proposal until demo exists")
        await emit_event("proposal_blocked", {
            "client_id": lead_id,
            "business_name": lead["business_name"],
            "reason": "demo_build_failed",
        })
        return

    # Generate proposal using Claude Sonnet (high quality, client-facing)
    proposal = await _generate_proposal(lead, demo_url)

    # Risk gate: proposal send is high-risk — graduated autonomy
    can_send = await gate_or_queue(
        "proposal_send",
        client_id=lead_id,
        content=proposal,
        item_type="proposal",
    )

    if can_send:
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
    else:
        logger.info(f"Proposal for {lead['business_name']} queued for review (risk gate)")


async def _build_demo_site(lead: dict) -> str:
    """Build a quick 1-page demo site for the prospect. Uses v0.dev API."""
    import httpx
    import os

    # Try v0.dev Platform API (project → chat → deploy)
    api_key = os.getenv("V0_API_KEY", "")
    if api_key:
        try:
            v0_headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            v0_base = "https://api.v0.dev/v1"

            async with httpx.AsyncClient(timeout=180.0) as client:
                # Step 1: Create project
                proj_resp = await client.post(
                    f"{v0_base}/projects",
                    json={"name": f"Demo - {lead['business_name'][:30]}"},
                    headers=v0_headers,
                )
                proj_resp.raise_for_status()
                project_id = proj_resp.json().get("id", "")

                if not project_id:
                    raise ValueError("No project ID returned from v0.dev")

                # Step 2: Create chat with initial prompt
                prompt = f"""Build a stunning 1-page landing site for:
Business: {lead['business_name']}
Industry: {lead.get('industry', 'general services')}
Location: {lead.get('city', '')}, {lead.get('country', '')}

This is a FREE demo to show the client what their site could look like.
Make it impressive: hero section, services, contact info, modern design.
Use a professional color scheme. Mobile responsive."""

                chat_resp = await client.post(
                    f"{v0_base}/chats",
                    json={
                        "initialMessage": prompt,
                        "projectId": project_id,
                    },
                    headers=v0_headers,
                )
                chat_resp.raise_for_status()
                chat_data = chat_resp.json()
                chat_id = chat_data.get("id", "")
                version_id = chat_data.get("versionId", chat_data.get("version_id", ""))

                if not chat_id:
                    raise ValueError("No chat ID returned from v0.dev")

                # Step 3: Deploy
                if version_id:
                    deploy_resp = await client.post(
                        f"{v0_base}/deployments",
                        json={
                            "projectId": project_id,
                            "chatId": chat_id,
                            "versionId": version_id,
                        },
                        headers=v0_headers,
                    )
                    deploy_resp.raise_for_status()
                    deploy_data = deploy_resp.json()
                    url = deploy_data.get("webUrl", deploy_data.get("url", ""))
                    if url:
                        logger.info(f"v0.dev demo deployed: {url}")
                        return url

        except Exception as e:
            logger.warning(f"v0.dev demo build failed: {e}")

    logger.warning("v0.dev API not configured — cannot build demo site")
    return ""


async def _generate_proposal(lead: dict, demo_url: str = "") -> dict:
    """Generate a custom proposal using Claude Sonnet."""
    demo_mention = f"\nI already built a demo site for you: {demo_url}" if demo_url else ""

    # Get learnings about what closes deals
    learnings = await get_relevant_learnings(
        "sales proposals, closing deals, pricing objections, what converts interested leads"
    )

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

PRICING:
- Professional 5-page website: $299
- Includes: custom design, mobile responsive, SEO optimized, contact forms
- Hosting available: $52/month
- Optional AI receptionist add-on: $398/month
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
        first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
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

    # Graduated autonomy: each sale unlocks more autonomous actions.
    # Emit milestone events so Hermes can notify Nico of new autonomy levels.
    from shared.risk_gate import refresh_policies
    await refresh_policies()

    milestones = {
        1: "Follow-ups and demo builds now autonomous",
        3: "Email sending now autonomous",
        5: "Proposals, invoices (<$500), and deploys now autonomous",
        10: "Full autonomy unlocked — all actions autonomous",
    }
    if sales in milestones:
        await emit_event("autonomy_milestone", {
            "sales_completed": sales,
            "unlocked": milestones[sales],
        })
        # Keep legacy review_mode in sync for backwards compatibility
        if sales >= 10:
            await set_config("review_mode", False)

    await emit_event("deal_closed", {"client_id": client_id, "sale_number": sales})
    logger.info(f"Sale #{sales} closed for client {client_id}!")
