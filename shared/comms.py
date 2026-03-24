"""
Inter-daemon communication layer.

All 4 daemons (Perseus, Titan, Hermes, ClawdBot) share:
1. task_queue   — request work from another daemon
2. events       — broadcast status/results to all daemons
3. titan_learnings — shared structured memory (what the system has learned)
4. Mem0 vector store — shared semantic memory (searchable by any daemon)
5. system_config — shared runtime configuration

This module provides a clean API for daemon-to-daemon communication.
"""

import json
import logging
import uuid
from typing import Any, Optional

from shared import db

logger = logging.getLogger("perseus.comms")


# ── Request Work From Another Daemon ──────────────────────────────

async def request_task(
    task_type: str,
    payload: dict = None,
    priority: int = 5,
    request_id: str = "",
    dedupe: bool = False,
    risk_level: str = "low",
) -> int:
    """
    Insert a task into the shared queue for any daemon to pick up.

    risk_level: "none", "low", "medium", "high", "critical"
    Tasks marked high/critical may be held for approval by the risk gate.

    Examples:
        # Titan asks ClawdBot to scrape a URL
        await request_task("web_scrape", {"url": "https://example.com"})

        # Perseus asks Titan to discover leads
        await request_task("lead_discovery", {"batch_size": 20})

        # Titan asks ClawdBot to enrich a lead
        await request_task("enrich_lead", {"client_id": 42})
    """
    full_payload = payload or {}
    if request_id:
        full_payload["request_id"] = request_id
    return await db.insert_task(task_type, full_payload, priority, dedupe=dedupe, risk_level=risk_level)


async def request_task_result(
    task_type: str,
    payload: dict = None,
    priority: int = 5,
    timeout_seconds: int = 60,
) -> Optional[dict]:
    """Request work from another daemon and wait for its task_result event."""
    request_id = uuid.uuid4().hex
    task_id = await request_task(
        task_type,
        payload=payload,
        priority=priority,
        request_id=request_id,
        dedupe=False,
    )
    if task_id is None:
        return None
    return await wait_for_event("task_result", request_id=request_id, timeout_seconds=timeout_seconds)


async def wait_for_event(
    event_type: str,
    request_id: str = "",
    timeout_seconds: int = 60,
) -> Optional[dict]:
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

async def broadcast(event_type: str, payload: dict = None, sender: str = ""):
    """
    Broadcast an event visible to all daemons.
    Hermes will also pick this up for Telegram alerts.
    """
    full_payload = {"sender": sender, **(payload or {})}
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
    source_lead_id: int = None,
):
    """
    Store a structured learning accessible by all daemons.

    Categories: discovery, email, sales, pricing, industry, delivery, system
    """
    await db.execute(
        """INSERT INTO titan_learnings (category, insight, confidence, source_lead_id, source_event)
           VALUES (%s, %s, %s, %s, %s)""",
        (category, insight, confidence, source_lead_id, source_agent),
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

async def store_vector_memory(content: str, category: str = "", metadata: dict = None):
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
        return await search_memory(query, limit)
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
           AND last_heartbeat > NOW() - INTERVAL '%s seconds'""",
        (agent_name, max_age_seconds),
    )
    return row is not None
