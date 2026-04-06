"""
Inter-daemon communication layer.

PRIMARY: A2A (HTTP JSON-RPC) for direct agent-to-agent calls.
FALLBACK: Postgres task_queue for backward compat when A2A unavailable.

All 4 daemons (Perseus, Titan, Hermes, ClawdBot) share:
1. A2A protocol  — direct HTTP calls between agents (primary)
2. task_queue     — request work from another daemon (fallback)
3. events         — broadcast status/results to all daemons
4. titan_learnings — shared structured memory (what the system has learned)
5. Mem0 vector store — shared semantic memory (searchable by any daemon)
6. system_config  — shared runtime configuration

This module provides a clean API for daemon-to-daemon communication.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from shared import db
from shared.observability import enrich_payload_with_context

logger = logging.getLogger("perseus.comms")

# Feature flag: set USE_A2A_DISPATCH=0 to disable A2A and use DB polling only
_USE_A2A = os.environ.get("USE_A2A_DISPATCH", "1") != "0"


# ── Memory Bus Event Schema (Phase 29) ──────────────────────────────

MEMORY_CHANGED_TOPIC = "memory.changed"


@dataclass
class MemoryChangedEvent:
    """Lightweight event fired after every memory write across all daemons.

    The payload is intentionally small (~200 bytes). MAGMA fetches the full
    record using ``table_name + record_id`` when it is ready to ingest.
    """

    source_daemon: str  # "titan" | "perseus" | "hermes" | "clawdbot" | "conway" | "deerflow"
    memory_type: str  # "learning" | "rule" | "observation" | "research" | "transaction" | "decision" | "memory" | "bandit"
    record_id: str | int  # primary key in source table
    table_name: str  # source Postgres table name
    action: str  # "insert" | "update" | "delete"
    visibility: str  # "public" | "scoped" | "restricted"
    summary: str | None = None  # optional 1-line summary for quick indexing
    timestamp: float = field(default_factory=time.time)


async def publish_memory_event(event: MemoryChangedEvent) -> None:
    """Publish a MemoryChangedEvent to the event bus.

    Gated behind the MEMORY_BUS_ENABLED feature flag.  When disabled,
    this is a no-op so callers can emit unconditionally.
    """
    try:
        enabled = await db.get_config("MEMORY_BUS_ENABLED", "true")
        if str(enabled).lower() not in ("true", "1", "yes"):
            return
    except (ConnectionError, RuntimeError, OSError):
        # DB unavailable — skip event emission, not critical
        return

    payload = {
        "source_daemon": event.source_daemon,
        "memory_type": event.memory_type,
        "record_id": str(event.record_id),
        "table_name": event.table_name,
        "action": event.action,
        "visibility": event.visibility,
        "summary": event.summary or "",
        "timestamp": event.timestamp,
    }
    await db.emit_event(MEMORY_CHANGED_TOPIC, payload)


# ── Request Work From Another Daemon ──────────────────────────────


async def _dispatch_a2a_task(
    task_type: str,
    payload: dict[str, Any],
    source_agent: str = "",
) -> int | str | None:
    """Attempt A2A dispatch for a task. IGUS-FIX: Extracted to reduce nesting (CWE-1124).

    Returns task_id on success, None if A2A unavailable or failed.
    Phase 30: governance check + audit trail on every dispatch.
    """
    agent_name = None
    try:
        from shared.capability_router import get_capability_router
        agent_name = await get_capability_router().route(task_type)
    # IGUS-FIX: Narrowed exception type (CWE-755)
    except (ImportError, KeyError, ValueError, AttributeError):
        pass
    if not agent_name:
        from shared.task_routing import TASK_ROUTING
        agent_name = TASK_ROUTING.get(task_type)
    if not agent_name:
        return None

    # Phase 30: Extract correlation context and effective source
    meta = payload.get("_meta", {}) if isinstance(payload.get("_meta"), dict) else {}
    correlation_id = str(meta.get("correlation_id", ""))
    effective_source = source_agent or payload.get("delegated_by", "") or "unknown"

    # Phase 30: Governance policy check
    try:
        from shared.middleware import governance_check

        cost_estimate = float(payload.get("estimated_cost", 0.0))
        allowed, reason = governance_check(
            effective_source, agent_name, capability=task_type,
            estimated_cost=cost_estimate,
        )
        if not allowed:
            logger.warning(
                "GOVERNANCE DENIED: %s -> %s (%s): %s",
                effective_source, agent_name, task_type, reason,
            )
            _fire_audit(
                effective_source, agent_name, task_type, "a2a",
                "denied", cost_estimate, correlation_id,
                {"reason": reason},
            )
            return None
    except (ImportError, AttributeError):
        pass  # Governance module unavailable — allow dispatch

    t0 = time.perf_counter()
    try:
        from shared.oj_bridge import call_agent_async
        result = await call_agent_async(agent_name, task_type, payload)
    # IGUS-FIX: Narrowed exception type (CWE-755)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, TimeoutError) as exc:
        logger.warning("A2A dispatch %s → %s failed, falling back to DB: %s", task_type, agent_name, exc)
        _fire_audit(
            effective_source, agent_name, task_type, "a2a",
            "allowed", 0.0, correlation_id,
            {"error": str(exc)},
        )
        return None

    if "error" in result:
        logger.warning("A2A dispatch %s → %s returned error: %s", task_type, agent_name, result.get("error"))
        _fire_audit(
            effective_source, agent_name, task_type, "a2a",
            "allowed", 0.0, correlation_id,
            {"error": result.get("error")},
        )
        return None

    duration_ms = int((time.perf_counter() - t0) * 1000)
    task_id = result.get("task_id")
    if not task_id:
        status = result.get("status", "unknown")
        task_id = f"a2a_{uuid.uuid4().hex[:8]}:{status}"
    logger.debug("A2A dispatch: %s → %s (status=%s)", task_type, agent_name, result.get("status", "ok"))

    # Phase 30: Audit trail — log successful dispatch
    _fire_audit(
        effective_source, agent_name, task_type, "a2a",
        "allowed", 0.0, correlation_id,
        {"duration_ms": duration_ms, "task_id": str(task_id)},
    )

    return task_id


def _fire_audit(
    source: str,
    target: str,
    action: str,
    protocol: str,
    policy_result: str,
    cost_estimate: float,
    correlation_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget audit log insert (Phase 30)."""
    try:
        import asyncio

        from shared.middleware import _log_governance_audit

        loop = asyncio.get_running_loop()
        loop.create_task(_log_governance_audit(
            source_agent=source,
            target_agent=target,
            action=action,
            protocol=protocol,
            policy_result=policy_result,
            cost_estimate=cost_estimate,
            correlation_id=correlation_id,
            metadata=metadata,
        ))
    except (RuntimeError, ImportError):
        pass  # No event loop or module unavailable


async def request_task(
    task_type: str,
    payload: dict[str, Any] | None = None,
    priority: int = 5,
    request_id: str = "",
    dedupe: bool = False,
) -> int | str | None:
    """
    Request work from another agent. Routes via A2A if available,
    falls back to DB task_queue insert.

    Returns task_id (int from DB, str from A2A) or None if deduped.
    """
    full_payload = payload or {}
    if request_id:
        full_payload["request_id"] = request_id
    full_payload = enrich_payload_with_context(full_payload, request_id=request_id)

    # Try A2A dispatch first (dynamic routing → static fallback)
    if _USE_A2A:
        a2a_result = await _dispatch_a2a_task(task_type, full_payload)
        if a2a_result is not None:
            return a2a_result

    # Fallback: DB task_queue
    return await db.insert_task(task_type, full_payload, priority, dedupe=dedupe)


async def request_task_result(
    task_type: str,
    payload: dict[str, Any] | None = None,
    priority: int = 5,
    timeout_seconds: int = 60,
) -> dict | None:
    """Request work from another agent and get the result.

    Via A2A: synchronous call-response (blocks until agent returns).
    Via DB: inserts task, polls events table for task_result event.
    """
    full_payload = payload or {}
    full_payload = enrich_payload_with_context(full_payload)

    # Try A2A direct call (synchronous request-response)
    if _USE_A2A:
        from shared.task_routing import TASK_ROUTING
        agent_name = TASK_ROUTING.get(task_type)
        if agent_name:
            try:
                from shared.oj_bridge import call_agent_async
                result = await call_agent_async(agent_name, task_type, full_payload, timeout=float(timeout_seconds))
                if "error" not in result:
                    logger.debug("A2A request_task_result: %s → %s (ok)", task_type, agent_name)
                    return result
                logger.warning("A2A request_task_result %s → %s error: %s", task_type, agent_name, result.get("error"))
            # IGUS-FIX: Narrowed exception type (CWE-755)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, TimeoutError) as exc:
                logger.warning("A2A request_task_result %s → %s failed, falling back to DB: %s", task_type, agent_name, exc)

    # Fallback: DB insert + poll
    request_id = uuid.uuid4().hex
    full_payload["request_id"] = request_id
    full_payload = enrich_payload_with_context(full_payload, request_id=request_id)
    task_id = await db.insert_task(task_type, full_payload, priority, dedupe=False)
    if task_id is None:
        return None
    return await wait_for_event("task_result", request_id=request_id, timeout_seconds=timeout_seconds)


async def wait_for_event(
    event_type: str,
    request_id: str = "",
    timeout_seconds: int = 60,
) -> dict | None:
    """
    Wait for a specific event (result from another daemon).
    Polls the events table. Returns the event payload or None on timeout.
    """
    import asyncio
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if request_id:
            event = await db.fetch_one(
                """SELECT id, payload FROM events
                   WHERE event_type = %s AND payload->>'request_id' = %s
                   AND acknowledged = FALSE
                   ORDER BY created_at DESC LIMIT 1""",
                (event_type, request_id),
            )
        else:
            event = await db.fetch_one(
                """SELECT id, payload FROM events
                   WHERE event_type = %s AND acknowledged = FALSE
                   ORDER BY created_at DESC LIMIT 1""",
                (event_type,),
            )
        if event:
            await db.execute(
                "UPDATE events SET acknowledged = TRUE WHERE id = %s",
                (event["id"],),
            )
            return event.get("payload", {})
        await asyncio.sleep(2)
    return None


# ── Broadcast to All Daemons ──────────────────────────────────────

async def broadcast(
    event_type: str,
    payload: dict[str, Any] | None = None,
    sender: str = "",
    exclude_sender: str | None = None,
) -> None:
    """
    Broadcast an event visible to all daemons.
    Hermes will also pick this up for Telegram alerts.

    Parameters
    ----------
    exclude_sender:
        When provided, stored in the payload as ``_exclude_sender`` so that
        consumers can skip events they themselves emitted.  This prevents
        daemons from processing their own broadcasts during polling loops.
    """
    full_payload = {"sender": sender, **(payload or {})}
    if exclude_sender:
        full_payload["_exclude_sender"] = exclude_sender
    full_payload = enrich_payload_with_context(full_payload)
    await db.emit_event(event_type, full_payload)


async def send_alert(message: str, sender: str = ""):
    """Send a high-priority alert that Hermes will forward to Nico."""
    await broadcast("urgent_alert", {"message": message, "sender": sender}, exclude_sender=sender or None)


# ── Typed Protocol Messages ──────────────────────────────────────


async def send_protocol_message(msg: Any) -> dict | None:
    """Send a typed protocol message to the recipient agent.

    Uses A2A direct call when available, falls back to event broadcast.
    Returns the response dict or None on failure.
    """
    if _USE_A2A:
        try:
            from shared.oj_bridge import call_agent_async
            result = await call_agent_async(
                msg.recipient,
                f"protocol.{msg.type.value}",
                msg.to_dict(),
                timeout=msg.deadline_seconds if hasattr(msg, "deadline_seconds") and msg.deadline_seconds else 30.0,
            )
            if isinstance(result, dict) and "error" not in result:
                return result
            logger.warning(
                "Protocol message %s -> %s error: %s",
                msg.type.value, msg.recipient,
                result.get("error") if isinstance(result, dict) else result,
            )
        except (ImportError, AttributeError, OSError, RuntimeError, ConnectionError, TimeoutError, TypeError) as exc:
            logger.warning("Protocol message %s -> %s A2A failed: %s", msg.type.value, msg.recipient, exc)

    # Fallback: broadcast as event
    await broadcast(f"protocol_{msg.type.value}", msg.to_dict(), sender=msg.sender)
    return None


# ── Shared Memory (Structured Learnings) ──────────────────────────

async def store_learning(
    category: str,
    insight: str,
    confidence: float = 0.5,
    source_agent: str = "",
    source_lead_id: int | None = None,
    source_event: str = "",
):
    """
    Store a structured learning accessible by all daemons.

    Categories: discovery, email, sales, pricing, industry, delivery, system
    """
    row = await db.fetch_one(
        """INSERT INTO titan_learnings (category, insight, confidence, source_lead_id, source_event, writer_agent)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (category, insight, confidence, source_lead_id, source_event, source_agent or "unknown"),
    )
    # Phase 29: emit memory.changed event
    if row:
        try:
            await publish_memory_event(MemoryChangedEvent(
                source_daemon=source_agent or "titan",
                memory_type="learning",
                record_id=row["id"],
                table_name="titan_learnings",
                action="insert",
                visibility="public",
                summary=insight[:120] if insight else category,
            ))
        except (ConnectionError, RuntimeError, OSError):
            pass  # Event emission is best-effort


async def get_learnings(category: str = "", limit: int = 10) -> list[dict]:
    """Get recent learnings, optionally filtered by category."""
    if category:
        return await db.fetch_all(
            """SELECT category, insight, confidence, created_at
               FROM titan_learnings
               WHERE category = %s
               ORDER BY confidence DESC, created_at DESC
               LIMIT %s""",
            (category, limit),
        )
    return await db.fetch_all(
        """SELECT category, insight, confidence, created_at
           FROM titan_learnings
           ORDER BY confidence DESC, created_at DESC
           LIMIT %s""",
        (limit,),
    )


# ── Shared Memory (Vector/Semantic via Mem0) ──────────────────────

async def store_vector_memory(content: str, category: str = "", metadata: dict[str, Any] | None = None) -> None:
    """Store a memory in the shared Mem0 vector store."""
    try:
        from titan.memory import store_memory
        await store_memory(content, category, metadata=metadata)
    except (ImportError, OSError, ValueError, RuntimeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"Vector memory store failed (non-critical): {e}")


async def search_vector_memory(query: str, limit: int = 5) -> list[dict]:
    """Search the shared vector memory."""
    try:
        from titan.memory import search_memory
        return [{"memory": item} for item in await search_memory(query, limit)]
    except (ImportError, OSError, ValueError, RuntimeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"Vector memory search failed (non-critical): {e}")
        return []


# ── Shared Config ──────────────────────────────────────────────────

async def get_shared_config(key: str, default: Any = None) -> Any:
    """Get a shared config value (accessible by all daemons)."""
    return await db.get_config(key, default)


async def set_shared_config(key: str, value: Any):
    """Set a shared config value."""
    await db.set_config(key, value)


# ── Agent Status ──────────────────────────────────────────────────

async def get_agent_status(agent_name: str = "") -> list[dict]:
    """Check if another daemon is alive."""
    if agent_name:
        return await db.fetch_all(
            "SELECT name, status, last_heartbeat FROM agent_registry WHERE name = %s",
            (agent_name,),
        )
    return await db.fetch_all(
        "SELECT name, status, last_heartbeat FROM agent_registry ORDER BY name"
    )


async def is_agent_alive(agent_name: str, max_age_seconds: int = 120) -> bool:
    """Check if a daemon's heartbeat is recent enough."""
    row = await db.fetch_one(
        """SELECT last_heartbeat FROM agent_registry
           WHERE name = %s AND status = 'active'
           AND last_heartbeat > NOW() - make_interval(secs => %s)""",
        (agent_name, max_age_seconds),
    )
    return row is not None


# ── Agent Decisions (auditable autonomous decision trail) ─────────

async def record_decision(
    agent: str,
    decision_type: str,
    context: dict,
    decision: dict,
    reasoning: str = "",
) -> int | None:
    """Record an autonomous decision for auditability and cross-agent visibility."""
    row = await db.fetch_one(
        """INSERT INTO agent_decisions (agent, decision_type, context, decision, reasoning)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        (agent, decision_type, json.dumps(context), json.dumps(decision), reasoning),
    )
    return row["id"] if row else None


async def record_decision_outcome(decision_id: int, outcome: dict) -> None:
    """Update a decision with its observed outcome (for closed-loop learning)."""
    await db.execute(
        "UPDATE agent_decisions SET outcome = %s WHERE id = %s",
        (json.dumps(outcome), decision_id),
    )


async def get_recent_decisions(agent: str = "", decision_type: str = "", limit: int = 10) -> list[dict]:
    """Read recent decisions, optionally filtered by agent or type."""
    if agent and decision_type:
        return await db.fetch_all(
            """SELECT * FROM agent_decisions
               WHERE agent = %s AND decision_type = %s
               ORDER BY created_at DESC LIMIT %s""",
            (agent, decision_type, limit),
        )
    if agent:
        return await db.fetch_all(
            "SELECT * FROM agent_decisions WHERE agent = %s ORDER BY created_at DESC LIMIT %s",
            (agent, limit),
        )
    return await db.fetch_all(
        "SELECT * FROM agent_decisions ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )


async def get_pending_recommendations(
    target_agent: str,
    since_minutes: int = 60,
    limit: int = 10,
) -> list[dict]:
    """Fetch unacknowledged recommendations addressed to target_agent."""
    rows = await db.fetch_all(
        """SELECT id, payload, created_at FROM events
           WHERE event_type = 'agent_recommendation'
           AND payload::jsonb->>'to' = %s
           AND created_at > NOW() - make_interval(mins => %s)
           ORDER BY created_at DESC LIMIT %s""",
        (target_agent, since_minutes, limit),
    )
    return [dict(r) for r in rows] if rows else []


async def request_help(
    from_agent: str,
    problem: str,
    context: dict | None = None,
) -> int | None:
    """An agent asks for help. Creates a task + decision record for visibility."""
    decision_id = await record_decision(
        agent=from_agent,
        decision_type="help_request",
        context={"problem": problem, **(context or {})},
        decision={"action": "requesting_help"},
        reasoning=problem,
    )
    await broadcast("agent_help_request", {
        "from": from_agent,
        "problem": problem,
        "decision_id": decision_id,
        **(context or {}),
    }, exclude_sender=from_agent)
    return decision_id


async def ask_agent(
    from_agent: str,
    to_agent: str,
    question: str,
    context: dict | None = None,
    timeout: int = 30,
) -> dict | None:
    """Ask another agent a question via A2A and get a synchronous response."""
    try:
        from shared.oj_bridge import call_agent_async
        result = await call_agent_async(
            to_agent,
            "ask",
            {"question": question, "from": from_agent, "context": context or {}},
            timeout=timeout,
        )
        return result
    except (ImportError, OSError, ValueError, RuntimeError, TimeoutError, KeyError, TypeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning(f"ask_agent({from_agent}→{to_agent}) failed: {e}")
        return None


async def call_agent_capability(
    to_agent: str,
    capability: str,
    params: dict,
    timeout: int = 30,
) -> dict | None:
    """Call a specific capability on an agent via A2A."""
    try:
        from shared.oj_bridge import call_agent_async
        result = await call_agent_async(
            to_agent,
            capability,
            params,
            timeout=timeout,
        )
        return result
    except (ImportError, OSError, ValueError, RuntimeError, TimeoutError, KeyError, TypeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning(f"call_agent_capability({to_agent}.{capability}) failed: {e}")
        return None


async def delegate_task(
    from_agent: str,
    to_agent: str,
    task_type: str,
    payload: dict | None = None,
    priority: int = 3,
) -> int | str | None:
    """Delegate a task to a specific agent with priority override.

    Unlike request_task(), this function routes directly to ``to_agent``
    instead of consulting TASK_ROUTING.  The caller explicitly chose the
    target agent and that choice is enforced here.

    Falls back to the DB task_queue (with ``delegated_to`` tag) only when
    A2A is unavailable so the task still lands in the right agent's queue.
    """
    full_payload = payload or {}
    full_payload["delegated_by"] = from_agent
    full_payload = enrich_payload_with_context(full_payload)

    # Primary: A2A direct call to the named agent (bypass TASK_ROUTING)
    if _USE_A2A:
        try:
            from shared.oj_bridge import call_agent_async
            result = await call_agent_async(to_agent, task_type, full_payload)
            if "error" not in result:
                task_id = result.get("task_id")
                if not task_id:
                    status = result.get("status", "unknown")
                    task_id = f"a2a_{uuid.uuid4().hex[:8]}:{status}"
                logger.info(
                    "Delegated %s from %s → %s via A2A (priority=%s, id=%s)",
                    task_type, from_agent, to_agent, priority, task_id,
                )
                return task_id
            logger.warning(
                "A2A delegation %s → %s returned error: %s",
                task_type, to_agent, result.get("error"),
            )
        # IGUS-FIX: Narrowed exception type (CWE-755)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, TimeoutError) as exc:
            logger.warning(
                "A2A delegation %s → %s failed, falling back to DB: %s",
                task_type, to_agent, exc,
            )

    # Fallback: DB task_queue tagged so the target agent can filter by it
    full_payload["delegated_to"] = to_agent
    task_id = await db.insert_task(task_type, full_payload, priority, dedupe=False)
    logger.info(
        "Delegated %s from %s → %s via DB fallback (priority=%s, id=%s)",
        task_type, from_agent, to_agent, priority, task_id,
    )
    return task_id


async def escalate_to_boss(
    from_agent: str,
    problem: str,
    context: dict | None = None,
) -> None:
    """Escalate a problem to the boss (OpenJarvis orchestrator)."""
    await ask_agent(from_agent, "orchestrator",
        f"Escalation from {from_agent}: {problem}",
        context=context, timeout=15)
    await send_alert(f"Escalation from {from_agent}: {problem[:200]}", sender=from_agent)
