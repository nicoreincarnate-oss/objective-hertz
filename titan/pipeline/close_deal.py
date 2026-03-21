"""
Stage 6-7: Close Deal + Demo Site
When a lead is interested: build a free demo site, handle sales conversation, close.
Uses Claude Sonnet for high-quality proposals.
"""

import json
import logging

from shared.db import fetch_all, fetch_one, execute, emit_event, get_config, set_config
from shared.llm_client import llm
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

    # Handle ongoing negotiations
    await _handle_negotiations()


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
        await execute(
            "UPDATE clients SET demo_site_url = %s WHERE id = %s",
            (demo_url, lead_id),
        )
        await transition_lead(lead_id, "demo_built")
    else:
        logger.warning(f"Could not build demo for lead {lead_id}, proceeding with proposal only")

    # Generate proposal using Claude Sonnet (high quality)
    proposal = await _generate_proposal(lead, demo_url)

    if needs_approval:
        # Queue for Nico's review
        await execute(
            """INSERT INTO review_queue (item_type, client_id, content, status)
               VALUES ('proposal', %s, %s, 'pending_review')""",
            (lead_id, json.dumps(proposal)),
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
- Hosting available: $29/month
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
    """Send the proposal email via Instantly campaign. Returns True if sent."""
    try:
        from tools.instantly_client import InstantlyClient
        from shared.db import get_config, set_config

        client = InstantlyClient()

        # Use a dedicated proposals campaign (or create one)
        campaign_id = await get_config("instantly_proposals_campaign_id", "")
        if not campaign_id:
            from datetime import datetime
            name = f"perseus-proposals-{datetime.now().strftime('%Y%m%d')}"
            campaign = await client.create_campaign(name)
            campaign_id = campaign.get("id", "")
            if campaign_id:
                await set_config("instantly_proposals_campaign_id", campaign_id)
                await client.activate_campaign(campaign_id)

        if not campaign_id:
            logger.error("No Instantly campaign available for proposals")
            await client.close()
            return False

        # Add lead to proposals campaign with proposal content as variables
        await client.add_lead(
            campaign_id=campaign_id,
            email=lead["email"],
            first_name=lead.get("contact_name", "").split()[0] if lead.get("contact_name") else "",
            company_name=lead.get("business_name", ""),
            personalization=proposal.get("body", "")[:500],
            custom_subject=proposal.get("subject", ""),
        )
        await client.close()

        await execute(
            "UPDATE clients SET last_contact_at = NOW() WHERE id = %s",
            (lead["id"],),
        )
        return True

    except Exception as e:
        logger.error(f"Proposal send failed for {lead.get('business_name', '?')}: {e}")
        return False


async def _handle_negotiations():
    """Handle ongoing negotiations — AI manages the sales conversation."""
    leads = await fetch_all(
        """SELECT id, business_name, email, research_summary, language
           FROM clients WHERE status IN ('proposal_sent', 'negotiating')
           AND last_contact_at < NOW() - INTERVAL '2 days'
           LIMIT 10"""
    )

    for lead in leads:
        # Check for replies
        # (This is handled by follow_up.py reply checker, but we can do targeted checks here)
        pass


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

    # Increment sales counter
    sales = await get_config("sales_completed", 0)
    await set_config("sales_completed", sales + 1)

    # Check if we should disable review mode
    threshold = await get_config("sales_before_autonomy", 10)
    if sales + 1 >= threshold:
        await set_config("review_mode", False)
        await emit_event("autonomy_unlocked", {
            "sales_completed": sales + 1,
            "message": "Review mode disabled — Titan is now fully autonomous!",
        })

    await emit_event("deal_closed", {"client_id": client_id, "sale_number": sales + 1})
    logger.info(f"Sale #{sales + 1} closed for client {client_id}!")
