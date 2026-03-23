"""Lightweight N8N webhook client for ClawdBot workflow orchestration.

ClawdBot can trigger N8N workflows via webhook, passing context and receiving results.
This lets specialist workflows (email verification, CRM sync, payment reminders, etc.)
run as N8N automations while ClawdBot orchestrates them.
"""

import logging
from typing import Any

import httpx

from shared.config import config
from tools.runtime_honesty import env_is_configured, truth_payload

logger = logging.getLogger("perseus.tools.n8n")

N8N_BASE = "http://localhost:5678"


def get_n8n_status() -> dict[str, Any]:
    """Check if N8N is reachable and configured."""
    try:
        resp = httpx.get(f"{N8N_BASE}/healthz", timeout=5.0)
        if resp.status_code == 200:
            return truth_payload(
                "live", "n8n_local", True,
                summary="N8N is running and reachable on localhost:5678.",
                provider="n8n",
            )
        return truth_payload(
            "degraded", "n8n_local", False,
            summary=f"N8N returned {resp.status_code}.",
            provider="n8n",
        )
    except Exception as e:
        return truth_payload(
            "blocked", "n8n_local", False,
            summary=f"N8N unreachable: {e}",
            provider="n8n",
        )


async def trigger_workflow(
    webhook_path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Trigger an N8N workflow via its webhook URL and return the result.

    Args:
        webhook_path: The webhook path (e.g. "/webhook/lead-enrichment")
        payload: JSON body to send to the workflow
        timeout: How long to wait for the workflow to complete

    Returns:
        The workflow's JSON response, or an error dict.
    """
    url = f"{N8N_BASE}{webhook_path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return {
                "ok": True,
                "status_code": resp.status_code,
                "result": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
            }
    except httpx.TimeoutException:
        return {"ok": False, "error": f"N8N workflow timed out after {timeout}s", "url": url}
    except Exception as e:
        logger.warning(f"N8N workflow trigger failed: {e}")
        return {"ok": False, "error": str(e), "url": url}


async def list_workflows() -> list[dict[str, Any]]:
    """List active N8N workflows (requires API access)."""
    import os
    user = os.getenv("N8N_USER", "")
    password = os.getenv("N8N_PASSWORD", "")
    if not user or not password:
        logger.warning("N8N_USER / N8N_PASSWORD not set — cannot list workflows")
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{N8N_BASE}/api/v1/workflows",
                auth=(user, password),
                params={"active": "true"},
            )
            resp.raise_for_status()
            data = resp.json()
            workflows = data.get("data", []) if isinstance(data, dict) else data
            return [
                {
                    "id": w.get("id", ""),
                    "name": w.get("name", ""),
                    "active": w.get("active", False),
                }
                for w in workflows
                if isinstance(w, dict)
            ]
    except Exception as e:
        logger.debug(f"N8N workflow list failed: {e}")
        return []
