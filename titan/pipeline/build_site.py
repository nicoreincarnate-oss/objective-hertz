"""
Stage 8: Build Website
Build a full 5-page website for closed deals using ClawdBot's 5-agent
competitive build process (parallel variants → Opus review → synthesis → v0 deploy).
"""

import logging

from shared.db import emit_event, execute, fetch_all
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.build_site")


async def build_sites():
    """Build websites for all closed deals."""
    leads = await fetch_all(
        """SELECT id, business_name, contact_name, industry,
                  research_summary, research_facts, language, country, city, demo_site_url
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
    """Build a full website using ClawdBot's 5-agent competitive process."""
    from clawdbot.site_builder import build_full_site
    return await build_full_site(lead)
