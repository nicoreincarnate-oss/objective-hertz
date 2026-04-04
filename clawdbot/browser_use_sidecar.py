"""HTTP client helpers for the local browser-use sidecar."""

from __future__ import annotations

import os
from typing import Any

import httpx


def _env_enabled(name: str, default: bool = True) -> bool:
	value = os.environ.get(name, "1" if default else "0").strip().lower()
	return value in {"1", "true", "yes", "on"}


def browser_use_enabled() -> bool:
	return _env_enabled("CLAWDBOT_BROWSER_USE_ENABLED", True)


def browser_use_base_url() -> str:
	return os.environ.get("BROWSER_USE_URL", "http://localhost:3031").strip().rstrip("/") or "http://localhost:3031"


def browser_use_session_name() -> str:
	return os.environ.get("CLAWDBOT_BROWSER_USE_SESSION", "clawdbot-browser").strip() or "clawdbot-browser"


async def browser_use_health(timeout: float = 10.0) -> dict[str, Any]:
	"""Probe the browser-use sidecar health endpoint."""
	snapshot: dict[str, Any] = {
		"enabled": browser_use_enabled(),
		"base_url": browser_use_base_url(),
		"session": browser_use_session_name(),
	}

	if not browser_use_enabled():
		snapshot["status"] = "disabled"
		return snapshot

	try:
		async with httpx.AsyncClient(timeout=timeout) as client:
			response = await client.get(f"{browser_use_base_url()}/health")
			snapshot["status_code"] = response.status_code
			data = response.json() if response.content else {}
			if isinstance(data, dict):
				snapshot.update(data)
				ok = bool(data.get("ok", True))
			else:
				ok = response.status_code < 400
			snapshot["status"] = "ready" if response.status_code < 400 and ok else "degraded"
	except Exception as exc:
		snapshot["status"] = "down"
		snapshot["error"] = str(exc)[:240]

	return snapshot


def _browser_use_task_prompt(payload: dict[str, Any]) -> str:
	objective = str(payload.get("objective") or payload.get("description") or payload.get("task") or "").strip()
	url = str(payload.get("url") or "").strip()
	session_name = str(payload.get("session_name") or "").strip()
	profile_name = str(payload.get("profile_name") or "").strip()
	auth_context = payload.get("auth_context") or {}
	research_mode = _is_research_objective(objective)

	parts = [objective or "browser flow"]
	if research_mode:
		parts.extend(_research_brief(payload))
	else:
		parts.append("Use the live browser-use session server for this task.")
	if url:
		parts.append(f"Target URL: {url}")
	if session_name:
		parts.append(f"Session name: {session_name}")
	if profile_name:
		parts.append(f"Profile name: {profile_name}")
	if auth_context:
		parts.append(f"Auth context: {auth_context}")
	return "\n".join(parts)


def _is_research_objective(objective: str) -> bool:
	text = objective.lower()
	return any(
		term in text
		for term in ("research", "investigate", "analyze", "analyse", "report", "compare", "find out", "look into")
	)


def _research_brief(payload: dict[str, Any]) -> list[str]:
	deliverable = str(payload.get("deliverable") or "concise research report with findings, sources, and recommended next actions").strip()
	success = str(payload.get("success_criteria") or "gather evidence, verify key claims, and return a structured summary").strip()
	constraints = payload.get("constraints") or []
	parts = [
		"Use the live browser-use session server in research mode.",
		"Before acting, form a short plan of what to verify, where to look, and what evidence to capture.",
		f"Deliverable: {deliverable}",
		f"Success criteria: {success}",
	]
	if isinstance(constraints, list) and constraints:
		parts.append(f"Constraints: {constraints}")
	return parts


async def run_browser_flow(payload: dict[str, Any], timeout: float = 900.0) -> dict[str, Any]:
	"""Send a browser_flow request to the browser-use sidecar."""
	if not browser_use_enabled():
		raise RuntimeError("browser-use sidecar is disabled")

	task = _browser_use_task_prompt(payload)
	request_body = {
		"task": task,
		"objective": str(payload.get("objective") or payload.get("description") or payload.get("task") or "").strip(),
		"url": str(payload.get("url") or "").strip(),
		"session_name": str(payload.get("session_name") or "").strip() or browser_use_session_name(),
		"profile_name": str(payload.get("profile_name") or "").strip(),
		"auth_context": payload.get("auth_context") or {},
	}

	async with httpx.AsyncClient(timeout=timeout) as client:
		response = await client.post(f"{browser_use_base_url()}/run", json=request_body)
		response.raise_for_status()
		result = response.json()

	if isinstance(result, dict):
		result.setdefault("status", "completed")
		result.setdefault("executor", "browser_use")
		return result

	return {
		"status": "completed",
		"executor": "browser_use",
		"result": result,
	}
