"""OpenJarvis bridge — singleton that boots OJ primitives for Perseus agents.

All agents import from here to get shared OJ infrastructure:
  - EventBus for pub/sub
  - A2A clients for calling other agents
  - TraceStore for observability
  - AgentManager for lifecycle
  - CapabilityPolicy for RBAC
  - WorkflowEngine for DAG execution
  - AuditLogger for tamper-proof audit trail

Usage:
    from shared.oj_bridge import get_bus, get_a2a_client, get_trace_store
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from shared.observability import ensure_trace_context

logger = logging.getLogger("perseus.oj_bridge")

# ── Agent A2A endpoints ──────────────────────────────────────────────

AGENT_URLS: dict[str, str] = {
    "titan": os.environ.get("TITAN_A2A_URL", "http://localhost:9001"),
    "hermes": os.environ.get("HERMES_A2A_URL", "http://localhost:9002"),
    "clawdbot": os.environ.get("CLAWDBOT_A2A_URL", "http://localhost:9003"),
    "orchestrator": os.environ.get("ORCHESTRATOR_A2A_URL", "http://localhost:9000"),
}

# ── Data directory ───────────────────────────────────────────────────

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)


# ── Singletons ───────────────────────────────────────────────────────

_bus = None
_trace_store = None
_agent_manager = None
_capability_policy = None
_audit_logger = None
_workflow_engine = None
_a2a_clients: dict[str, object] = {}


def get_bus():
    """Get the shared OJ EventBus singleton."""
    global _bus
    if _bus is None:
        from openjarvis.core.events import get_event_bus
        _bus = get_event_bus()
        logger.info("OJ EventBus initialized")
    return _bus


def get_a2a_client(agent_name: str):
    """Get an A2AClient for calling a remote agent by name.

    Returns None if agent URL is not configured.
    """
    if agent_name not in AGENT_URLS:
        return None
    if agent_name not in _a2a_clients:
        from openjarvis.a2a.client import A2AClient
        url = AGENT_URLS[agent_name]
        _a2a_clients[agent_name] = A2AClient(url, timeout=120.0)
        logger.info("A2AClient created for %s at %s", agent_name, url)
    return _a2a_clients[agent_name]


def get_a2a_tool(agent_name: str):
    """Get an A2AAgentTool that wraps a remote agent as an OJ BaseTool.

    This lets OrchestratorAgent or WorkflowEngine invoke remote agents
    as tools in their tool-calling loop.
    """
    client = get_a2a_client(agent_name)
    if client is None:
        return None
    from openjarvis.a2a.tool import A2AAgentTool
    return A2AAgentTool(client, name=f"a2a_{agent_name}")


def get_trace_store():
    """Get the shared TraceStore for recording execution traces."""
    global _trace_store
    if _trace_store is None:
        from openjarvis.traces.store import TraceStore
        db_path = str(_DATA_DIR / "traces.sqlite")
        _trace_store = TraceStore(db_path)
        # Auto-save traces from EventBus
        _trace_store.subscribe_to_bus(get_bus())
        logger.info("OJ TraceStore initialized at %s", db_path)
    return _trace_store


def get_agent_manager():
    """Get the shared AgentManager for lifecycle tracking."""
    global _agent_manager
    if _agent_manager is None:
        from openjarvis.agents.manager import AgentManager
        try:
            from openjarvis.core.config import load_config

            db_path = load_config().agent_manager.db_path
        except Exception:
            db_path = str(_DATA_DIR / "agents.sqlite")
        _agent_manager = AgentManager(db_path)
        logger.info("OJ AgentManager initialized at %s", db_path)
    return _agent_manager


def get_capability_policy():
    """Get the shared CapabilityPolicy for RBAC enforcement."""
    global _capability_policy
    if _capability_policy is None:
        from openjarvis.security.capabilities import CapabilityPolicy
        _capability_policy = CapabilityPolicy(default_deny=True)
        # Grant agent-specific capabilities
        _capability_policy.grant("titan", "pipeline:*", "*")
        _capability_policy.grant("titan", "db:clients", "*")
        _capability_policy.grant("titan", "db:email_sequences", "*")
        _capability_policy.grant("titan", "network:instantly", "*")
        _capability_policy.grant("clawdbot", "browser:*", "*")
        _capability_policy.grant("clawdbot", "skill:*", "*")
        _capability_policy.grant("clawdbot", "network:firecrawl", "*")
        _capability_policy.grant("clawdbot", "file:write", "*")
        _capability_policy.grant("hermes", "channel:telegram", "*")
        _capability_policy.grant("hermes", "channel:dashboard", "*")
        _capability_policy.grant("hermes", "db:events", "*")
        logger.info("OJ CapabilityPolicy initialized with agent grants")
    return _capability_policy


def get_audit_logger():
    """Get the shared AuditLogger with Merkle chain integrity."""
    global _audit_logger
    if _audit_logger is None:
        from openjarvis.security.audit import AuditLogger
        db_path = str(_DATA_DIR / "audit.sqlite")
        _audit_logger = AuditLogger(db_path)
        # Subscribe to security events on the bus
        _audit_logger.subscribe_to_bus(get_bus())
        logger.info("OJ AuditLogger initialized at %s", db_path)
    return _audit_logger


def get_workflow_engine():
    """Get the shared WorkflowEngine for DAG-based pipeline execution."""
    global _workflow_engine
    if _workflow_engine is None:
        from openjarvis.workflow.engine import WorkflowEngine
        _workflow_engine = WorkflowEngine(bus=get_bus(), max_parallel=4)
        logger.info("OJ WorkflowEngine initialized")
    return _workflow_engine


def call_agent(agent_name: str, capability: str, params: Optional[dict] = None, timeout: float = 120.0) -> dict:
    """Call a remote agent's capability via A2A and return parsed result.

    This is the primary function for inter-agent communication.
    Replaces the old pattern of inserting into task_queue and polling.

    Args:
        agent_name: "titan", "hermes", or "clawdbot"
        capability: The capability to invoke (e.g., "lead_discovery", "alert_urgent")
        params: Parameters to pass to the capability handler
        timeout: Maximum seconds to wait for response

    Returns:
        Parsed JSON dict from the agent's response, or {"error": ...} on failure.
    """
    import json

    client = get_a2a_client(agent_name)
    if client is None:
        return {"error": f"No A2A URL configured for agent '{agent_name}'"}

    request_params = dict(params or {})
    meta = ensure_trace_context(
        request_id=str(request_params.get("request_id", "") or ""),
        task_id=str(request_params.get("task_id", "") or ""),
    )
    existing_meta = request_params.get("_meta", {}) if isinstance(request_params.get("_meta", {}), dict) else {}
    request_params["_meta"] = {
        **existing_meta,
        **meta,
    }
    payload = json.dumps({"capability": capability, "params": request_params})
    try:
        task = client.send_task(
            payload,
            request_id=meta["request_id"],
            headers={
                "X-Trace-Id": meta["trace_id"],
                "X-Correlation-Id": meta["correlation_id"],
                "X-Request-Id": meta["request_id"],
            },
            metadata=meta,
        )
        if task.state in ("completed", "working"):
            try:
                return json.loads(task.output_text)
            except (json.JSONDecodeError, TypeError):
                return {"result": task.output_text}
        return {"error": f"A2A task state: {task.state}", "output": task.output_text}
    except Exception as exc:
        logger.warning("A2A call to %s/%s failed: %s", agent_name, capability, exc)
        return {"error": str(exc)}


async def call_agent_async(agent_name: str, capability: str, params: Optional[dict] = None, timeout: float = 120.0) -> dict:
    """Async wrapper around call_agent (runs sync A2A call in thread pool)."""
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, call_agent, agent_name, capability, params, timeout)


def forward_event_to_hermes(event_type: str, payload: dict) -> None:
    """Forward an event to Hermes for alert dispatch via A2A.

    Non-blocking — failures are logged and ignored (DB fallback still works).
    """
    try:
        meta = ensure_trace_context(
            request_id=str(payload.get("request_id", "") or ""),
            task_id=str(payload.get("task_id", "") or ""),
        )
        call_agent("hermes", "event_forward", {
            "event_type": event_type,
            "_meta": meta,
            **payload,
        })
    except Exception as exc:
        logger.debug("Event forward to Hermes failed (DB fallback active): %s", exc)
