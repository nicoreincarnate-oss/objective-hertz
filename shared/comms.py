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

import json
import logging
import os
import uuid
from typing import Any

from shared import db
from shared.observability import enrich_payload_with_context

logger = logging.getLogger("perseus.comms")

# Feature flag: set USE_A2A_DISPATCH=0 to disable A2A and use DB polling only
_USE_A2A = os.environ.get("USE_A2A_DISPATCH", "1") != "0"


# ── Request Work From Another Daemon ──────────────────────────────

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
        agent_name = None
        try:
            from shared.capability_router import get_capability_router
            agent_name = await get_capability_router().route(task_type)
        except Exception:
            pass
        if not agent_name:
            from shared.task_routing import TASK_ROUTING
            agent_name = TASK_ROUTING.get(task_type)
        if agent_name:
            try:
                from shared.oj_bridge import call_agent_async
                result = await call_agent_async(agent_name, task_type, full_payload)
                if "error" not in result:
                    task_id = result.get("task_id")
                    if not task_id:
                        # Store the full result so callers can inspect status.
                        # Generate an ID for tracking but tag it with the actual
                        # status so callers don't confuse "dispatched" with "succeeded".
                        status = result.get("status", "unknown")
                        task_id = f"a2a_{uuid.uuid4().hex[:8]}:{status}"
                    logger.debug("A2A dispatch: %s → %s (status=%s)", task_type, agent_name, result.get("status", "ok"))
                    return task_id
                logger.warning("A2A dispatch %s → %s returned error: %s", task_type, agent_name, result.get("error"))
            except Exception as exc:
                logger.warning("A2A dispatch %s → %s failed, falling back to DB: %s", task_type, agent_name, exc)

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
            except Exception as exc:
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

async def broadcast(event_type: str, payload: dict[str, Any] | None = None, sender: str = "") -> None:
    """
    Broadcast an event visible to all daemons.
    Hermes will also pick this up for Telegram alerts.
    """
    full_payload = {"sender": sender, **(payload or {})}
    full_payload = enrich_payload_with_context(full_payload)
    await db.emit_event(event_type, full_payload)


async def send_alert(message: str, sender: str = ""):
    """Send a high-priority alert that Hermes will forward to Nico."""
    await broadcast("urgent_alert", {"message": message, "sender": sender})


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
    await db.execute(
        """INSERT INTO titan_learnings (category, insight, confidence, source_lead_id, source_event, writer_agent)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (category, insight, confidence, source_lead_id, source_event, source_agent or "unknown"),
    )


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
    except Exception as e:
        logger.debug(f"Vector memory store failed (non-critical): {e}")


async def search_vector_memory(query: str, limit: int = 5) -> list[dict]:
    """Search the shared vector memory."""
    try:
        from titan.memory import search_memory
        return [{"memory": item} for item in await search_memory(query, limit)]
    except Exception as e:
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
    })
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
    except Exception as e:
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
    except Exception as e:
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
        except Exception as exc:
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
