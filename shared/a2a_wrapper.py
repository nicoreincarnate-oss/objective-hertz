"""Reusable A2A server wrapper for Perseus daemons.

Creates a FastAPI app that serves the OpenJarvis-native A2A protocol:
- GET  /.well-known/agent.json  → AgentCard
- POST /a2a/tasks               → JSON-RPC 2.0 dispatch to daemon handler
- GET  /a2a/capabilities        → detailed capability list
- GET  /a2a/health              → daemon health check

Each daemon implements ONE method: ``async def handle_a2a(self, input_text: str) -> str``
The A2A server runs alongside the daemon's existing async loop via asyncio.gather().
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.observability import (
    bind_context_from_payload,
    clear_observability_context,
    ensure_trace_context,
)

logger = logging.getLogger("perseus.a2a")


# ── A2A protocol types (mirrors OpenJarvis a2a/protocol.py) ──────────


class TaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class AgentCard:
    """Agent discovery card served at /.well-known/agent.json."""

    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    capabilities: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    authentication: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "authentication": self.authentication,
        }


# ── A2A app factory ──────────────────────────────────────────────────


def create_a2a_app(
    agent_card: AgentCard,
    handler: Callable[[str], Awaitable[str]],
    health_check: Optional[Callable[[], Awaitable[dict]]] = None,
    capability_details: Optional[List[Dict[str, Any]]] = None,
) -> FastAPI:
    """Create a FastAPI app serving the OpenJarvis-native A2A protocol.

    Parameters
    ----------
    agent_card:
        The daemon's AgentCard (name, description, capabilities).
    handler:
        Async function that takes input_text and returns output_text.
        This is the daemon's ``handle_a2a(input_text) -> str`` method.
    health_check:
        Optional async function returning health dict.
    capability_details:
        Optional list of rich capability descriptions for /a2a/capabilities.
    """
    app = FastAPI(title=f"{agent_card.name} A2A Server", docs_url=None, redoc_url=None)
    tasks: Dict[str, Dict[str, Any]] = {}

    @app.get("/.well-known/agent.json")
    async def get_agent_card():
        return JSONResponse(agent_card.to_dict())

    @app.post("/a2a/tasks")
    async def handle_task(request: Request):
        """Handle JSON-RPC 2.0 A2A task requests."""
        body = await request.json()
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id", "")

        if method == "tasks/send":
            return await _handle_send(params, req_id)
        elif method == "tasks/get":
            return await _handle_get(params, req_id)
        elif method == "tasks/cancel":
            return await _handle_cancel(params, req_id)
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": req_id,
            })

    async def _handle_send(params: dict, req_id: str) -> JSONResponse:
        """Handle tasks/send — extract text, call handler, return result.

        Security: scans input for prompt injection before processing.
        Tracing: records execution to OJ TraceStore via EventBus.
        """
        import time as _time

        # Extract input text from OpenJarvis message format
        input_text = ""
        message = params.get("message", {})
        if isinstance(message, dict):
            parts = message.get("parts", [])
            if parts and isinstance(parts[0], dict):
                input_text = parts[0].get("text", "")
        if not input_text:
            input_text = params.get("input", "")

        request_meta = params.get("metadata", {}) if isinstance(params.get("metadata", {}), dict) else {}
        if isinstance(params.get("_meta", {}), dict):
            request_meta = {
                **request_meta,
                **params.get("_meta", {}),
            }
        header_trace = request.headers.get("x-trace-id", "").strip()[:64]
        header_correlation = request.headers.get("x-correlation-id", "").strip()[:64]
        header_request = (request.headers.get("x-request-id", "").strip() or req_id)[:64]
        context = ensure_trace_context(
            trace_id=str(request_meta.get("trace_id", "") or header_trace),
            correlation_id=str(request_meta.get("correlation_id", "") or header_correlation),
            task_id=str(request_meta.get("task_id", "")),
            request_id=str(request_meta.get("request_id", "") or header_request),
        )

        task_id = uuid.uuid4().hex[:16]
        task = {
            "id": task_id,
            "state": TaskState.WORKING.value,
            "input": input_text,
            "output": "",
            "history": [],
            "metadata": {
                **context,
            },
        }
        tasks[task_id] = task
        bind_context_from_payload({"_meta": {**context, "task_id": task_id}})

        # Security: scan for prompt injection
        try:
            from openjarvis.security.injection_scanner import InjectionScanner
            from openjarvis.security.types import ThreatLevel
            scanner = InjectionScanner()
            scan_result = scanner.scan(input_text)
            if not scan_result.is_clean and scan_result.threat_level in (ThreatLevel.HIGH, ThreatLevel.CRITICAL):
                logger.warning(
                    "A2A injection detected in %s: %s (threat=%s)",
                    agent_card.name,
                    [f.pattern_name for f in scan_result.findings],
                    scan_result.threat_level.value,
                )
                task["output"] = json.dumps({"error": "Request blocked by injection scanner"})
                task["state"] = TaskState.FAILED.value
                task["metadata"]["blocked_by"] = "injection_scanner"
                return JSONResponse({"jsonrpc": "2.0", "result": task, "id": req_id})
        except ImportError as e:
            logger.debug("Injection scan failed: %s", e)

        t0 = _time.time()
        try:
            result = await handler(input_text)
            task["output"] = result
            task["state"] = TaskState.COMPLETED.value
            task["history"].append({"role": "agent", "content": result})
        except Exception as exc:
            logger.error("A2A handler error: %s", exc, exc_info=True)
            task["output"] = str(exc)
            task["state"] = TaskState.FAILED.value
        duration = _time.time() - t0

        # Tracing: publish A2A execution event
        try:
            from openjarvis.core.events import EventType
            from shared.oj_bridge import get_bus
            get_bus().publish(EventType.A2A_TASK_COMPLETED, {
                "agent": agent_card.name,
                "task_id": task_id,
                "state": task["state"],
                "duration_seconds": duration,
                "input_length": len(input_text),
                "output_length": len(task.get("output", "")),
                **context,
            })
        except Exception as e:
            logger.debug("Injection scan failed: %s", e)
        finally:
            clear_observability_context()

        # Prune old tasks (keep last 100)
        if len(tasks) > 100:
            oldest_keys = sorted(tasks.keys())[:len(tasks) - 100]
            for k in oldest_keys:
                tasks.pop(k, None)

        return JSONResponse({
            "jsonrpc": "2.0",
            "result": task,
            "id": req_id,
        })

    async def _handle_get(params: dict, req_id: str) -> JSONResponse:
        task_id = params.get("id", "")
        task = tasks.get(task_id)
        if not task:
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Task not found: {task_id}"},
                "id": req_id,
            })
        return JSONResponse({"jsonrpc": "2.0", "result": task, "id": req_id})

    async def _handle_cancel(params: dict, req_id: str) -> JSONResponse:
        task_id = params.get("id", "")
        task = tasks.get(task_id)
        if not task:
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Task not found: {task_id}"},
                "id": req_id,
            })
        task["state"] = TaskState.CANCELED.value
        return JSONResponse({"jsonrpc": "2.0", "result": task, "id": req_id})

    @app.get("/a2a/capabilities")
    async def get_capabilities():
        """Rich capability descriptions (optional discovery endpoint)."""
        if capability_details:
            return JSONResponse({"capabilities": capability_details})
        return JSONResponse({
            "capabilities": [
                {"name": cap} for cap in agent_card.capabilities
            ],
        })

    @app.get("/a2a/health")
    async def get_health():
        if health_check:
            return JSONResponse(await health_check())
        return JSONResponse({"status": "ok", "agent": agent_card.name})

    return app


__all__ = ["AgentCard", "create_a2a_app"]
