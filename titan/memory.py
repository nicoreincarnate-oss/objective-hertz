"""
Titan's learning and memory system.

Three layers:
1. STRUCTURED MEMORY (Postgres titan_learnings) — text insights with category + confidence
2. VECTOR MEMORY (Qdrant via Mem0) — semantic search across all interactions
3. TRAINING DATA (Postgres training_data) — raw examples for LoRA fine-tuning

Daily reflection analyzes what worked. Weekly review shifts strategy.
Every interaction feeds the training data collector for future fine-tuning.
"""

import json
import logging
import time
from datetime import datetime

from shared.db import fetch_all, fetch_one, execute, fetch_val, emit_event
from shared.llm_client import llm
from shared.config import config

logger = logging.getLogger("perseus.titan.memory")

MEM0_ALERT_COOLDOWN_SECONDS = 3600
_last_mem0_alert_at: dict[str, float] = {
    "store": 0.0,
    "search": 0.0,
}


# ── Vector Memory (Mem0 + Qdrant) ─────────────────────────────────────

async def store_memory(content: str, category: str, client_id: int = None, metadata: dict = None):
    """Store a memory in Mem0 for vector-searchable retrieval."""
    import httpx
    mem_metadata = {
        "category": category,
        "client_id": client_id,
        "timestamp": datetime.now().isoformat(),
    }
    if metadata:
        mem_metadata.update(metadata)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{config.memory.mem0_host}/v1/memories/",
                json={
                    "messages": [{"role": "assistant", "content": content}],
                    "user_id": "titan",
                    "metadata": mem_metadata,
                },
            )
    except Exception as e:
        logger.warning(f"Mem0 store failed: {e}")
        await _emit_mem0_alert(
            "store",
            "Mem0 write failed; vector learning storage is degraded.",
            error=e,
            context={"category": category, "client_id": client_id},
        )


async def search_memory(query: str, limit: int = 5) -> list[str]:
    """Search vector memory for relevant past experiences."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{config.memory.mem0_host}/v1/memories/search/",
                json={"query": query, "user_id": "titan", "limit": limit},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return [r.get("memory", "") for r in results if r.get("memory")]
    except Exception as e:
        logger.debug(f"Mem0 search failed (non-critical): {e}")
        return []


async def _emit_mem0_alert(kind: str, message: str, *, error: Exception, context: dict | None = None) -> None:
    """Emit a throttled Mem0 degradation alert so learning failures are visible."""
    now = time.monotonic()
    last = _last_mem0_alert_at.get(kind, 0.0)
    if now - last < MEM0_ALERT_COOLDOWN_SECONDS:
        return

    _last_mem0_alert_at[kind] = now
    payload = {
        "kind": kind,
        "message": message,
        "error": str(error)[:200],
        "mem0_host": config.memory.mem0_host,
    }
    if context:
        payload.update(context)

    await emit_event("memory_write_failed", payload)
    await emit_event("urgent_alert", {
        "sender": "titan.memory",
        "message": f"{message} Error: {str(error)[:160]}",
    })


async def get_relevant_learnings(context: str, limit: int = 10) -> str:
    """
    Get relevant learnings for a specific context.
    Combines structured DB learnings + vector memory search.
    Used by pipeline stages to inform their decisions.
    """
    # 1. Structured learnings from Postgres (high confidence first)
    db_learnings = await fetch_all(
        """SELECT category, insight FROM titan_learnings
           WHERE confidence > 0.5
           ORDER BY confidence DESC, created_at DESC LIMIT %s""",
        (limit,),
    )

    # 2. Vector search from Mem0
    vector_memories = await search_memory(context, limit=limit)

    # Combine
    parts = []
    if db_learnings:
        parts.append("STRUCTURED INSIGHTS:")
        for l in db_learnings:
            parts.append(f"  [{l['category']}] {l['insight']}")
    if vector_memories:
        parts.append("RELEVANT MEMORIES:")
        for m in vector_memories:
            parts.append(f"  - {m}")

    return "\n".join(parts) if parts else "No prior learnings yet."


# ── Daily Reflection ──────────────────────────────────────────────────

async def daily_reflection():
    """
    Daily analysis: what worked, what didn't, extract actionable insights.
    Stores in both Postgres (structured) and Mem0 (vector).
    """
    logger.info("Starting daily reflection...")

    metrics = await _gather_daily_metrics()
    if not metrics.get("emails_sent"):
        logger.info("No activity to reflect on today")
        return

    # Get recent training outcomes for context
    outcomes = await fetch_all(
        """SELECT example_type, outcome, COUNT(*) as count
           FROM training_data
           WHERE created_at > NOW() - INTERVAL '24 hours'
           GROUP BY example_type, outcome"""
    )

    prompt = f"""You are Titan's learning engine. Analyze today's performance:

METRICS:
{json.dumps(metrics, indent=2, default=str)}

TRAINING OUTCOMES (last 24h):
{json.dumps([dict(o) for o in outcomes], default=str) if outcomes else 'No outcomes tracked yet'}

Extract 3-5 actionable insights. Be specific:
- What email subject patterns got opens?
- What industries responded?
- What time of day got replies?
- What follow-up approach worked?
- What should we change tomorrow?

Return JSON list:
[{{"category": "email_performance|targeting|timing|copywriting|industry", "insight": "specific actionable finding", "confidence": 0.0-1.0}}]"""

    result = await llm.generate(prompt, model="smart", temperature=0.4)
    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        insights = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse daily reflection")
        return

    # Store in Postgres
    for insight in insights:
        await execute(
            """INSERT INTO titan_learnings (category, insight, confidence)
               VALUES (%s, %s, %s)""",
            (insight.get("category", "general"), insight.get("insight", ""), insight.get("confidence", 0.5)),
        )

    # Store in Mem0 for vector search
    combined = "; ".join(i.get("insight", "") for i in insights)
    await store_memory(
        f"Daily reflection {datetime.now().strftime('%Y-%m-%d')}: {combined}",
        category="daily_reflection",
    )

    await emit_event("daily_reflection_complete", {"insights": len(insights)})
    logger.info(f"Daily reflection: {len(insights)} insights stored")


# ── Weekly Strategy Review ────────────────────────────────────────────

async def weekly_strategy_review():
    """Weekly deep analysis for strategic shifts."""
    logger.info("Starting weekly strategy review...")

    weekly_metrics = await _gather_weekly_metrics()
    weekly_learnings = await fetch_all(
        """SELECT category, insight, confidence
           FROM titan_learnings
           WHERE created_at > NOW() - INTERVAL '7 days'
           ORDER BY confidence DESC"""
    )

    # Training data stats
    training_stats = await fetch_one(
        """SELECT
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE outcome = 'positive') as positive,
               COUNT(*) FILTER (WHERE outcome = 'negative') as negative
           FROM training_data
           WHERE created_at > NOW() - INTERVAL '7 days'"""
    )

    prompt = f"""You are Titan's strategic brain. Weekly review:

METRICS THIS WEEK:
{json.dumps(weekly_metrics, indent=2, default=str)}

DAILY INSIGHTS THIS WEEK:
{json.dumps([dict(l) for l in weekly_learnings], default=str)}

TRAINING DATA: {dict(training_stats) if training_stats else 'None'}

Provide strategic recommendations:
1. Should we change target industries? Which ones convert best?
2. Should we adjust pricing ($299 for 5-page site)? Up or down?
3. What email style/tone is working best?
4. What geographies are converting best?
5. Should we increase/decrease email volume?
6. Is there enough training data for a LoRA fine-tune?

Return JSON:
{{"strategy_changes": ["..."], "pricing_recommendation": "...",
  "top_industry": "...", "top_region": "...",
  "email_notes": "...", "ready_for_training": true/false,
  "training_recommendation": "..."}}"""

    result = await llm.generate(prompt, model="smart", temperature=0.3)
    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        strategy = json.loads(result[start:end])

        await execute(
            """INSERT INTO titan_learnings (category, insight, confidence)
               VALUES ('weekly_strategy', %s, 0.8)""",
            (json.dumps(strategy),),
        )

        await store_memory(
            f"Weekly strategy {datetime.now().strftime('%Y-%m-%d')}: {json.dumps(strategy)}",
            category="weekly_strategy",
        )

        # If AI recommends training, trigger it
        if strategy.get("ready_for_training"):
            from titan.training import run_lora_training
            await run_lora_training()

        await emit_event("weekly_review_complete", {"strategy": strategy})
        logger.info("Weekly strategy review stored")
    except Exception as e:
        logger.warning(f"Failed to parse weekly strategy: {e}")


# ── Metrics Gathering ─────────────────────────────────────────────────

async def _gather_daily_metrics() -> dict:
    """Gather today's performance metrics."""
    emails_sent = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    opens = await fetch_val(
        "SELECT COALESCE(SUM(opens), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    replies = await fetch_val(
        "SELECT COALESCE(SUM(replies), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    new_leads = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE DATE(created_at) = CURRENT_DATE"
    ) or 0
    interested = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'interested' AND DATE(updated_at) = CURRENT_DATE"
    ) or 0
    closed = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'closed' AND DATE(updated_at) = CURRENT_DATE"
    ) or 0
    revenue = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid' AND DATE(paid_at) = CURRENT_DATE"
    ) or 0

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "emails_sent": emails_sent,
        "opens": opens,
        "open_rate": round(opens / max(emails_sent, 1) * 100, 1),
        "replies": replies,
        "reply_rate": round(replies / max(emails_sent, 1) * 100, 1),
        "new_leads": new_leads,
        "interested_today": interested,
        "closed_today": closed,
        "revenue_today": float(revenue),
    }


async def _gather_weekly_metrics() -> dict:
    """Gather this week's metrics."""
    row = await fetch_one(
        """SELECT
               COALESCE(SUM(emails_sent), 0) as total_sent,
               COALESCE(SUM(opens), 0) as total_opens,
               COALESCE(SUM(replies), 0) as total_replies,
               COALESCE(SUM(interested_replies), 0) as total_interested
           FROM outreach_metrics
           WHERE date > CURRENT_DATE - INTERVAL '7 days'"""
    )
    revenue = await fetch_val(
        """SELECT COALESCE(SUM(amount), 0) FROM deals
           WHERE status = 'paid' AND paid_at > NOW() - INTERVAL '7 days'"""
    ) or 0

    result = dict(row) if row else {}
    result["revenue_this_week"] = float(revenue)
    return result
