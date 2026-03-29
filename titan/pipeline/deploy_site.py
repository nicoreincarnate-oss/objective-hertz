"""
Stage 9: Deploy Website
Deploy built sites to hosting. AI picks best platform.
"""

import logging

from shared.comms import request_task_result
from shared.db import emit_event, fetch_all, get_config

logger = logging.getLogger("perseus.titan.deploy")


async def deploy_sites():
    """Verify deployed sites and trigger redeploy when a site is down.

    build_site.py handles initial deployment. This stage monitors deployed
    sites and takes real action when verification fails — dispatching a
    rebuild task and emitting a trackable event, not just logging a warning.
    """
    if await get_config("shadow_mode", False):
        logger.info("SHADOW: deploy verification skipped (nothing deployed in shadow mode)")
        return

    leads = await fetch_all(
        """SELECT id, business_name, final_site_url
           FROM clients WHERE status = 'deployed' AND final_site_url IS NOT NULL
           ORDER BY created_at ASC LIMIT 5"""
    )

    for lead in leads:
        is_live = await _verify_deployment(lead["final_site_url"])
        if is_live:
            logger.info(f"Site confirmed live for {lead['business_name']}: {lead['final_site_url']}")
        else:
            logger.warning(f"Site not live for {lead['business_name']}: {lead['final_site_url']} — dispatching redeploy")
            await emit_event("site_redeploy_needed", {
                "client_id": lead["id"],
                "business_name": lead["business_name"],
                "failed_url": lead["final_site_url"],
            })
            # Dispatch a rebuild task so the site gets redeployed
            try:
                await request_task_result(
                    "build_sites",
                    payload={
                        "client_id": lead["id"],
                        "rebuild": True,
                        "reason": f"site_down:{lead['final_site_url']}",
                    },
                    timeout_seconds=120,
                )
            except Exception as e:
                logger.error(f"Redeploy dispatch failed for {lead['business_name']}: {e}")


async def _verify_deployment(url: str) -> bool:
    """Check if a deployed site is accessible via ClawdBot verification.

    Does NOT fall back to a bare HTTP 200 check. If ClawdBot can't verify
    the site (A2A down, timeout, handler error), this returns False so the
    caller treats it as unverified rather than silently assuming "live."
    """
    if not url:
        return False
    try:
        task_result = await request_task_result(
            "verify_single_site",
            payload={"url": url},
            timeout_seconds=30,
        )
        if task_result is None:
            logger.warning(f"Site verification unavailable for {url} — treating as unverified")
            return False

        # ClawdBot's handle_site_verify returns is_live directly in the result
        is_live = task_result.get("is_live") or task_result.get("result", {}).get("is_live")
        if not is_live:
            status_code = task_result.get("status_code") or task_result.get("result", {}).get("status_code", "?")
            logger.warning(f"Site verification failed for {url}: status_code={status_code}")
        return bool(is_live)
    except Exception as e:
        logger.warning(f"Site verification error for {url}: {e}")
        return False
