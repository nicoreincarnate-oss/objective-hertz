"""
Stage 8: Build Website
Build a full 5-page website for closed deals via v0.dev Platform API.
"""

import logging

from shared.db import fetch_all, execute, emit_event
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.build_site")


async def build_sites():
    """Build websites for all closed deals."""
    leads = await fetch_all(
        """SELECT id, business_name, contact_name, industry,
                  research_summary, language, country, city, demo_site_url
           FROM clients WHERE status = 'closed'
           ORDER BY created_at ASC LIMIT 3"""
    )

    for lead in leads:
        try:
            await transition_lead(lead["id"], "building")
            url = await _build_full_site(lead)
            if url:
                await execute(
                    "UPDATE clients SET final_site_url = %s WHERE id = %s",
                    (url, lead["id"]),
                )
                await transition_lead(lead["id"], "deployed")
                await emit_event("site_deployed", {
                    "client_id": lead["id"],
                    "business_name": lead["business_name"],
                    "url": url,
                })
                logger.info(f"Built and deployed site for {lead['business_name']}: {url}")
            else:
                logger.error(f"Site build failed for lead {lead['id']}")
        except Exception as e:
            logger.error(f"Build failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("build_site", e, lead_id=lead["id"])


async def _build_full_site(lead: dict) -> str:
    """Build a full 5-page website via v0.dev Platform API (project → chat → deploy)."""
    import httpx
    import os

    api_key = os.getenv("V0_API_KEY", "")
    if not api_key:
        logger.warning("V0_API_KEY not set — cannot build sites")
        return ""

    v0_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    v0_base = "https://api.v0.dev/v1"

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            # Step 1: Create project
            proj_resp = await client.post(
                f"{v0_base}/projects",
                json={"name": f"Site - {lead['business_name'][:30]}"},
                headers=v0_headers,
            )
            proj_resp.raise_for_status()
            project_id = proj_resp.json().get("id", "")

            if not project_id:
                raise ValueError("No project ID returned from v0.dev")

            # Step 2: Create chat with site generation prompt
            prompt = f"""Build a professional 5-page website for:
Business: {lead['business_name']}
Industry: {lead.get('industry', 'general services')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Contact: {lead.get('contact_name', '')}

Pages: Home, About, Services, Gallery/Portfolio, Contact
Style: Modern, professional, mobile-responsive
Include: Hero section, service descriptions, contact form, testimonials section, footer with business info"""

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

            # Step 3: Deploy to Vercel
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
                    logger.info(f"v0.dev site deployed: {url}")
                    return url

    except Exception as e:
        logger.error(f"v0.dev site build failed: {e}")

    return ""
