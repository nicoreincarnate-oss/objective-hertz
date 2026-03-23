"""Titan A2A Server — exposes all Titan capabilities via OpenJarvis A2A protocol.

The handler accepts natural language OR JSON-structured requests and routes
to the appropriate Titan function.  OpenJarvis sees Titan as ONE tool —
the LLM sends text, Titan figures out what to do.
"""

from __future__ import annotations

import json
import logging

from shared import comms, db
from shared.a2a_wrapper import AgentCard, create_a2a_app
from shared.pipeline import assess_pipeline_state

logger = logging.getLogger("perseus.titan.a2a")


# ── Agent Card ────────────────────────────────────────────────────────

TITAN_CARD = AgentCard(
    name="titan",
    description=(
        "Autonomous revenue engine — 10-stage pipeline from lead discovery to invoice. "
        "Can discover leads, research companies, compose emails, send campaigns, "
        "track follow-ups, close deals, build sites, deploy, and invoice. "
        "Also tracks budget, stores learnings, and logs all decisions."
    ),
    url="http://localhost:9001",
    version="1.0.0",
    capabilities=[
        "ask", "pipeline_status", "lead_discovery", "lead_research",
        "email_compose", "email_send", "follow_up_check",
        "close_interested", "build_sites", "process_invoices",
        "sync_analytics", "budget_status", "budget_check",
        "lead_search", "lead_details", "lead_count",
        "learnings_query", "learnings_store",
        "decisions_query", "memory_search", "memory_store",
        "config_get", "config_set",
        "task_dispatch", "health_check",
        "events_recent", "event_relay",
        "deliverability_check", "daily_reflection",
        "morning_briefing",
    ],
)


# ── Capability handlers ───────────────────────────────────────────────

async def _pipeline_status(**_) -> dict:
    return await assess_pipeline_state()


async def _dispatch_task(task_type: str, payload: dict | None = None, priority: int = 5, **_) -> dict:
    task_id = await db.insert_task(task_type, payload or {}, priority)
    return {"dispatched": True, "task_id": task_id, "task_type": task_type}


async def _budget_status(**_) -> dict:
    try:
        from tools.budget_guard import BudgetGuard
        guard = BudgetGuard()
        return await guard.check_budget()
    except Exception as e:
        return {"error": str(e)}


async def _lead_search(status: str = "", industry: str = "", min_score: float = 0, limit: int = 20, **_) -> list:
    conditions = []
    params = []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if industry:
        conditions.append("industry ILIKE %s")
        params.append(f"%{industry}%")
    if min_score > 0:
        conditions.append("COALESCE(lead_score, 0) >= %s")
        params.append(min_score)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    rows = await db.fetch_all(
        f"SELECT id, business_name, email, status, industry, lead_score FROM clients {where} ORDER BY updated_at DESC LIMIT %s",
        tuple(params),
    )
    return [dict(r) for r in rows]


async def _lead_details(client_id: int = 0, **_) -> dict | None:
    if not client_id:
        return {"error": "client_id required"}
    row = await db.fetch_one("SELECT * FROM clients WHERE id = %s", (client_id,))
    return dict(row) if row else {"error": f"Lead {client_id} not found"}


async def _lead_count(**_) -> dict:
    rows = await db.fetch_all("SELECT status, COUNT(*) as count FROM clients GROUP BY status ORDER BY count DESC")
    return {r["status"]: r["count"] for r in rows}


async def _learnings_query(category: str = "", limit: int = 10, **_) -> list:
    results = await comms.get_learnings(category, limit)
    return [dict(r) for r in results]


async def _learnings_store(category: str = "", insight: str = "", confidence: float = 0.5, **_) -> dict:
    await comms.store_learning(category, insight, confidence, source_agent="titan_a2a")
    return {"stored": True}


async def _decisions_query(agent: str = "", decision_type: str = "", limit: int = 10, **_) -> list:
    results = await comms.get_recent_decisions(agent, decision_type, limit)
    return [dict(r) for r in results]


async def _memory_search(query: str = "", limit: int = 5, **_) -> list:
    return await comms.search_vector_memory(query, limit)


async def _memory_store(content: str = "", category: str = "", **_) -> dict:
    await comms.store_vector_memory(content, category)
    return {"stored": True}


async def _config_get(key: str = "", default=None, **_) -> dict:
    value = await db.get_config(key, default)
    return {"key": key, "value": value}


async def _config_set(key: str = "", value=None, **_) -> dict:
    await db.set_config(key, value)
    return {"set": True, "key": key}


async def _health_check(**_) -> dict:
    return {"status": "running", "agent": "titan"}


async def _run_lead_discovery(batch_size: int = 20, **_) -> dict:
    from titan.pipeline.lead_discovery import discover_leads
    ids = await discover_leads(batch_size=batch_size)
    return {"discovered": len(ids), "client_ids": ids}


async def _run_lead_research(batch_size: int = 10, **_) -> dict:
    from titan.pipeline.lead_research import research_leads
    await research_leads(batch_size=batch_size)
    return {"researched": True}


async def _run_email_compose(batch_size: int = 20, **_) -> dict:
    from titan.pipeline.email_compose import compose_emails
    await compose_emails(batch_size=batch_size)
    return {"composed": True}


async def _run_email_send(batch_size: int = 50, **_) -> dict:
    from titan.pipeline.email_send import send_emails
    await send_emails(batch_size=batch_size)
    return {"sent": True}


async def _run_follow_up(**_) -> dict:
    from titan.pipeline.follow_up import process_follow_ups
    await process_follow_ups()
    return {"followed_up": True}


async def _run_close_interested(**_) -> dict:
    from titan.pipeline.close_deal import process_interested_leads
    await process_interested_leads()
    return {"closed": True}


async def _run_build_sites(**_) -> dict:
    from titan.pipeline.build_site import build_sites
    await build_sites()
    return {"built": True}


async def _run_process_invoices(**_) -> dict:
    from titan.pipeline.invoice import process_invoices
    await process_invoices()
    return {"invoiced": True}


async def _run_sync_analytics(**_) -> dict:
    from titan.pipeline.email_send import sync_campaign_analytics
    await sync_campaign_analytics()
    return {"synced": True}


async def _run_deliverability_check(**_) -> dict:
    from titan.deliverability import monitor_deliverability
    await monitor_deliverability()
    return {"checked": True}


async def _run_daily_reflection(**_) -> dict:
    from titan.memory import daily_reflection
    await daily_reflection()
    return {"reflected": True}


async def _ask(question: str = "", from_agent: str = "", context: dict = None, **_) -> dict:
    """Handle a question from another agent about pipeline/lead/budget state."""
    from shared.db import fetch_all, fetch_val
    from shared.llm_client import llm

    # Gather context for answering
    pipeline_summary = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status != 'dead'"
    ) or 0
    recent_events = await fetch_all(
        "SELECT event_type, payload FROM events ORDER BY created_at DESC LIMIT 5"
    )
    events_ctx = "\n".join(
        f"- {e['event_type']}: {str(e.get('payload', ''))[:100]}"
        for e in (recent_events or [])
    )

    prompt = (
        f"You are Titan, the revenue pipeline agent. {from_agent} is asking you:\n\n"
        f"{question}\n\n"
        f"Context from {from_agent}: {json.dumps(context or {})}\n\n"
        f"Current pipeline: {pipeline_summary} active leads\n"
        f"Recent events:\n{events_ctx}\n\n"
        f"Answer concisely and factually."
    )

    answer = await llm.generate(prompt, model="fast", max_tokens=300)
    return {"answer": answer, "from": "titan"}


async def _events_recent(limit: int = 20, **_) -> list:
    """Return recent events for this agent."""
    rows = await db.fetch_all(
        "SELECT event_type, payload, created_at FROM events ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    return [dict(r) for r in rows]


async def _event_relay(type: str = "", payload: dict = None, source: str = "", **_) -> dict:
    """Accept a relayed event from OpenJarvis and store it."""
    if not type:
        return {"error": "event type is required"}
    await db.emit_event(f"relayed_{type}", {
        "source": source,
        "original_type": type,
        **(payload or {}),
    })
    return {"status": "relayed", "type": type, "source": source}


CAPABILITY_HANDLERS = {
    "ask": _ask,
    "pipeline_status": _pipeline_status,
    "lead_discovery": _run_lead_discovery,
    "lead_research": _run_lead_research,
    "email_compose": _run_email_compose,
    "email_send": _run_email_send,
    "follow_up_check": _run_follow_up,
    "close_interested": _run_close_interested,
    "build_sites": _run_build_sites,
    "process_invoices": _run_process_invoices,
    "sync_analytics": _run_sync_analytics,
    "budget_status": _budget_status,
    "budget_check": _budget_status,
    "lead_search": _lead_search,
    "lead_details": _lead_details,
    "lead_count": _lead_count,
    "learnings_query": _learnings_query,
    "learnings_store": _learnings_store,
    "decisions_query": _decisions_query,
    "memory_search": _memory_search,
    "memory_store": _memory_store,
    "config_get": _config_get,
    "config_set": _config_set,
    "task_dispatch": _dispatch_task,
    "health_check": _health_check,
    "events_recent": _events_recent,
    "event_relay": _event_relay,
    "deliverability_check": _run_deliverability_check,
    "daily_reflection": _run_daily_reflection,
    "morning_briefing": lambda **p: _dispatch_task("morning_briefing", p),
}


# ── Handler: routes text → capability ─────────────────────────────────

async def handle_a2a(input_text: str) -> str:
    """Route A2A requests to Titan capabilities.

    Accepts:
    1. JSON: {"capability": "pipeline_status", "params": {...}}
    2. Natural language: "pipeline status", "discover 20 leads", etc.
    """
    text = input_text.strip()

    # Try JSON-structured input first
    try:
        req = json.loads(text)
        if isinstance(req, dict) and "capability" in req:
            cap = req["capability"]
            params = req.get("params", {})
            handler = CAPABILITY_HANDLERS.get(cap)
            if handler:
                result = await handler(**params)
                return json.dumps(result, indent=2, default=str)
            return json.dumps({"error": f"Unknown capability: {cap}"})
    except (json.JSONDecodeError, TypeError) as e:
        logger.debug("JSON decode failed: %s", e)

    # Natural language routing (keyword matching)
    lower = text.lower()

    if any(w in lower for w in ["pipeline status", "pipeline state", "assess pipeline", "how's the pipeline"]):
        result = await _pipeline_status()
    elif "budget" in lower and ("status" in lower or "check" in lower or "how" in lower):
        result = await _budget_status()
    elif "lead count" in lower or "how many leads" in lower:
        result = await _lead_count()
    elif "health" in lower or "alive" in lower or "status" in lower:
        result = await _health_check()
    elif "discover" in lower and "lead" in lower:
        batch = _extract_number(lower, default=20)
        result = await _dispatch_task("lead_discovery", {"batch_size": batch})
    elif "research" in lower and "lead" in lower:
        result = await _dispatch_task("lead_research", {})
    elif "compose" in lower and "email" in lower:
        result = await _dispatch_task("email_compose", {})
    elif "send" in lower and "email" in lower:
        result = await _dispatch_task("email_send", {})
    elif "follow" in lower and "up" in lower:
        result = await _dispatch_task("follow_up_check", {})
    elif "close" in lower and ("interested" in lower or "deal" in lower):
        result = await _dispatch_task("close_interested", {})
    elif "build" in lower and "site" in lower:
        result = await _dispatch_task("build_sites", {})
    elif "invoice" in lower:
        result = await _dispatch_task("process_invoices", {})
    elif "briefing" in lower or "morning" in lower:
        result = await _dispatch_task("morning_briefing", {})
    elif "learning" in lower and ("search" in lower or "query" in lower or "what" in lower):
        category = ""
        for cat in ["discovery", "email", "sales", "pricing", "industry", "delivery", "system"]:
            if cat in lower:
                category = cat
                break
        result = await _learnings_query(category=category)
    elif "decision" in lower:
        result = await _decisions_query()
    elif "search" in lower and "lead" in lower:
        result = await _lead_search()
    elif "search" in lower and "memory" in lower:
        query = text.replace("search memory for", "").replace("search memory", "").strip()
        result = await _memory_search(query=query)
    else:
        result = {
            "error": "Titan couldn't route this request. Try JSON format: "
                     '{"capability": "pipeline_status", "params": {}}',
            "available_capabilities": list(CAPABILITY_HANDLERS.keys()),
        }

    return json.dumps(result, indent=2, default=str)


def _extract_number(text: str, default: int = 20) -> int:
    """Extract the first number from a text string."""
    import re
    match = re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else default


# ── Factory ───────────────────────────────────────────────────────────

def create_titan_a2a(titan_daemon=None) -> "FastAPI":
    """Create Titan's A2A FastAPI app."""
    health_fn = titan_daemon.health_check if titan_daemon else None
    return create_a2a_app(
        agent_card=TITAN_CARD,
        handler=handle_a2a,
        health_check=health_fn,
    )
