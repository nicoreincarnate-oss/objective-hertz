"""
Stage 9: Deploy Website
Deploy built sites to hosting. AI picks best platform.
"""

import logging

from shared.comms import request_task_result
from shared.db import fetch_all, execute, emit_event
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.deploy")


async def deploy_sites():
    """Deploy any sites that are built but not yet live."""
    # Note: build_site.py already handles deployment in most cases
    # This stage handles re-deployments, domain changes, etc.
    leads = await fetch_all(
        """SELECT id, business_name, final_site_url
           FROM clients WHERE status = 'deployed' AND final_site_url IS NOT NULL
           ORDER BY created_at ASC LIMIT 5"""
    )

    for lead in leads:
        # Verify the site is actually live
        is_live = await _verify_deployment(lead["final_site_url"])
        if is_live:
            logger.info(f"Site confirmed live for {lead['business_name']}: {lead['final_site_url']}")
        else:
            logger.warning(f"Site not live for {lead['business_name']}, may need re-deploy")


async def _verify_deployment(url: str) -> bool:
    """Check if a deployed site is accessible."""
    if not url:
        return False
    try:
        task_result = await request_task_result(
            "verify_single_site",
            payload={"url": url},
            timeout_seconds=30,
        )
        if task_result and task_result.get("ok"):
            return bool(task_result.get("result", {}).get("is_live"))

        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False
