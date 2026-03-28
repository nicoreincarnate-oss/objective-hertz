"""
Stage 8: Build Website
Build a full 5-page website for closed deals using ClawdBot's 5-agent
competitive build process (parallel variants → Opus review → synthesis → v0 deploy).
"""

import logging

from shared.db import emit_event, execute, fetch_all, get_config
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.build_site")


def _site_build_fail_open_when_qa_unavailable() -> bool:
    try:
        from shared.config import config

        return bool(getattr(getattr(config, "site_build", object()), "fail_open_when_qa_unavailable", False))
    except Exception:
        return False


async def build_sites():
    """Build websites for all closed deals."""
    # Shadow mode: build sites locally but skip Netlify deploy and QA verification
    shadow = await get_config("shadow_mode", False)

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

            # Shadow mode: site was built locally, skip deploy/QA
            if shadow and url:
                logger.info(
                    "SHADOW: built site for %s locally — skipping deploy. URL: %s",
                    lead["business_name"], url,
                )
                await emit_event("shadow_site_built", {
                    "client_id": lead["id"],
                    "business_name": lead["business_name"],
                    "local_url": url,
                })
                continue

            if url:
                from shared.comms import request_task_result

                qa_result = await request_task_result(
                    "verify_demo_site",
                    payload={
                        "url": url,
                        "business_name": lead["business_name"],
                        "client_id": lead["id"],
                        "site_type": "full",
                    },
                    timeout_seconds=60,
                )

                if qa_result and qa_result.get("ok") and qa_result.get("result", {}).get("passed"):
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
                elif qa_result and qa_result.get("ok"):
                    reason = qa_result.get("result", {}).get("reason", "qa_failed")
                    await emit_event("site_deploy_blocked", {
                        "client_id": lead["id"],
                        "business_name": lead["business_name"],
                        "url": url,
                        "reason": f"full_site_qa_failed: {reason}",
                    })
                    logger.warning(f"Blocked deployment for {lead['business_name']} after QA failure: {reason}")
                elif _site_build_fail_open_when_qa_unavailable():
                    await execute(
                        "UPDATE clients SET final_site_url = %s WHERE id = %s",
                        (url, lead["id"]),
                    )
                    await transition_lead(lead["id"], "deployed")
                    await emit_event("site_deployed", {
                        "client_id": lead["id"],
                        "business_name": lead["business_name"],
                        "url": url,
                        "warning": "full_site_qa_unavailable_fail_open",
                    })
                    logger.warning(f"Full-site QA unavailable, deploying {lead['business_name']} due to fail-open config")
                else:
                    await emit_event("site_deploy_blocked", {
                        "client_id": lead["id"],
                        "business_name": lead["business_name"],
                        "url": url,
                        "reason": "full_site_qa_unavailable",
                    })
                    logger.warning(f"Blocked deployment for {lead['business_name']} because full-site QA was unavailable")
            else:
                logger.error(f"Site build failed for lead {lead['id']}")
        except Exception as e:
            logger.error(f"Build failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("build_site", e, lead_id=lead["id"])


async def _build_full_site(lead: dict) -> str:
    """Build a full website using ClawdBot's build_full_site capability via A2A."""
    from shared.comms import call_agent_capability
    result = await call_agent_capability(
        "clawdbot", "build_full_site", {"lead": lead}, timeout=120,
    )
    if not result or result.get("status") == "error":
        raise RuntimeError(f"ClawdBot site build failed: {result}")
    return result.get("url", "")
