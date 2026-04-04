"""Lightweight N8N webhook client for ClawdBot workflow orchestration.

ClawdBot can trigger N8N workflows via webhook, passing context and receiving results.
This lets specialist workflows (email verification, CRM sync, payment reminders, etc.)
run as N8N automations while ClawdBot orchestrates them.
"""

import logging
from typing import Any

import httpx

from tools.runtime_honesty import truth_payload

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


# ── Auth helper ──────────────────────────────────────────────────

def _get_auth() -> tuple[str, str] | None:
    """Return (user, password) for N8N basic auth, or None if not configured."""
    import os
    user = os.getenv("N8N_USER", "")
    password = os.getenv("N8N_PASSWORD", "")
    if not user or not password:
        logger.warning("N8N_USER / N8N_PASSWORD not set — cannot authenticate")
        return None
    return (user, password)


# ── CRUD Methods ─────────────────────────────────────────────────

async def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Get a single workflow by ID.

    Args:
        workflow_id: The N8N workflow ID.

    Returns:
        The workflow dict, or an error dict on failure.
    """
    auth = _get_auth()
    if auth is None:
        return {"ok": False, "error": "N8N credentials not configured"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{N8N_BASE}/api/v1/workflows/{workflow_id}",
                auth=auth,
            )
            resp.raise_for_status()
            return {"ok": True, "workflow": resp.json()}
    except Exception as e:
        logger.warning(f"N8N get_workflow failed: {e}")
        return {"ok": False, "error": str(e)}


async def create_workflow(definition: dict) -> str:
    """Create a new N8N workflow.

    Args:
        definition: Workflow JSON definition (name, nodes, connections, etc.).

    Returns:
        The created workflow ID, or empty string on failure.
    """
    auth = _get_auth()
    if auth is None:
        return ""

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{N8N_BASE}/api/v1/workflows",
                auth=auth,
                json=definition,
            )
            resp.raise_for_status()
            data = resp.json()
            wf_id = str(data.get("id", ""))
            logger.info(f"Created N8N workflow: {wf_id}")
            return wf_id
    except Exception as e:
        logger.warning(f"N8N create_workflow failed: {e}")
        return ""


async def update_workflow(workflow_id: str, definition: dict) -> dict[str, Any]:
    """Update an existing N8N workflow.

    Args:
        workflow_id: The workflow to update.
        definition: Updated workflow definition.

    Returns:
        The updated workflow dict, or an error dict.
    """
    auth = _get_auth()
    if auth is None:
        return {"ok": False, "error": "N8N credentials not configured"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(
                f"{N8N_BASE}/api/v1/workflows/{workflow_id}",
                auth=auth,
                json=definition,
            )
            resp.raise_for_status()
            return {"ok": True, "workflow": resp.json()}
    except Exception as e:
        logger.warning(f"N8N update_workflow failed: {e}")
        return {"ok": False, "error": str(e)}


async def delete_workflow(workflow_id: str) -> bool:
    """Delete a workflow by ID.

    Args:
        workflow_id: The workflow to delete.

    Returns:
        True on success, False on failure.
    """
    auth = _get_auth()
    if auth is None:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{N8N_BASE}/api/v1/workflows/{workflow_id}",
                auth=auth,
            )
            resp.raise_for_status()
            logger.info(f"Deleted N8N workflow: {workflow_id}")
            return True
    except Exception as e:
        logger.warning(f"N8N delete_workflow failed: {e}")
        return False


async def activate_workflow(workflow_id: str) -> dict[str, Any]:
    """Activate a workflow so it responds to triggers.

    Args:
        workflow_id: The workflow to activate.

    Returns:
        The updated workflow dict, or an error dict.
    """
    auth = _get_auth()
    if auth is None:
        return {"ok": False, "error": "N8N credentials not configured"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{N8N_BASE}/api/v1/workflows/{workflow_id}",
                auth=auth,
                json={"active": True},
            )
            resp.raise_for_status()
            return {"ok": True, "workflow": resp.json()}
    except Exception as e:
        logger.warning(f"N8N activate_workflow failed: {e}")
        return {"ok": False, "error": str(e)}


async def deactivate_workflow(workflow_id: str) -> dict[str, Any]:
    """Deactivate a workflow.

    Args:
        workflow_id: The workflow to deactivate.

    Returns:
        The updated workflow dict, or an error dict.
    """
    auth = _get_auth()
    if auth is None:
        return {"ok": False, "error": "N8N credentials not configured"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{N8N_BASE}/api/v1/workflows/{workflow_id}",
                auth=auth,
                json={"active": False},
            )
            resp.raise_for_status()
            return {"ok": True, "workflow": resp.json()}
    except Exception as e:
        logger.warning(f"N8N deactivate_workflow failed: {e}")
        return {"ok": False, "error": str(e)}


async def import_workflow_from_json(json_path: str) -> str:
    """Import a workflow from a JSON file.

    Args:
        json_path: Absolute path to the workflow JSON file.

    Returns:
        The created workflow ID, or empty string on failure.
    """
    import json
    import pathlib

    path = pathlib.Path(json_path)
    if not path.exists():
        logger.warning(f"Workflow JSON not found: {json_path}")
        return ""

    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read workflow JSON: {e}")
        return ""

    return await create_workflow(definition)


async def export_workflow_to_json(workflow_id: str, output_path: str) -> str:
    """Export a workflow to a JSON file.

    Args:
        workflow_id: The workflow to export.
        output_path: Absolute path for the output JSON file.

    Returns:
        The output file path on success, or empty string on failure.
    """
    import json
    import pathlib

    result = await get_workflow(workflow_id)
    if not result.get("ok"):
        logger.warning(f"Cannot export workflow {workflow_id}: {result.get('error')}")
        return ""

    try:
        out = pathlib.Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(result["workflow"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Exported workflow {workflow_id} to {output_path}")
        return str(out)
    except OSError as e:
        logger.warning(f"Failed to write workflow JSON: {e}")
        return ""
