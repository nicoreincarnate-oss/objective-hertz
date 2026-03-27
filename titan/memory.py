"""
Titan's learning and memory system.

Three layers:
1. STRUCTURED MEMORY (Postgres titan_learnings) — text insights with category + confidence
2. VECTOR MEMORY (Qdrant via Mem0) — semantic search across all interactions
3. TRAINING DATA (Postgres training_data) — raw examples for LoRA fine-tuning

Daily reflection analyzes what worked. Weekly review shifts strategy.
Every interaction feeds the training data collector for future fine-tuning.
"""

import hashlib
import json
import logging
import time
from datetime import datetime

from shared.config import config
from shared.db import emit_event, execute, fetch_all, fetch_one, fetch_val
from shared.llm_client import llm

logger = logging.getLogger("perseus.titan.memory")

MEM0_ALERT_COOLDOWN_SECONDS = 3600
_last_mem0_alert_at: dict[str, float] = {
    "store": 0.0,
    "search": 0.0,
}


# ── Vector Memory (Mem0 + Qdrant) ─────────────────────────────────────

async def store_memory(
    content: str,
    category: str,
    client_id: int | None = None,
    metadata: dict | None = None,
    outcome_magnitude: float = 0.5,
    sample_size: int = 1,
) -> None:
    """Store a memory in Mem0 for vector-searchable retrieval.

    outcome_magnitude and sample_size feed the importance score so retrieval
    surfaces statistically significant memories first, not just similar ones.
    """
    import httpx
    importance = _compute_importance(outcome_magnitude, sample_size)
    mem_metadata = {
        "category": category,
        "client_id": client_id,
        "timestamp": datetime.now().isoformat(),
        "importance": importance,
        "sample_size": sample_size,
    }
    if metadata:
        mem_metadata.update(metadata)
    # Namespace by client_id so each client's memories are isolated in Mem0.
    # "titan" is the fallback for system-level memories (reflections, strategies).
    mem0_user_id = f"client:{client_id}" if client_id else "titan"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            await http.post(
                f"{config.memory.mem0_host}/v1/memories/",
                json={
                    "messages": [{"role": "assistant", "content": content}],
                    "user_id": mem0_user_id,
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


async def search_memory(query: str, limit: int = 5, client_id: int | None = None) -> list[str]:
    """Search vector memory for relevant past experiences.

    When client_id is provided, searches the client's namespace first,
    then backfills remaining slots from the system ("titan") namespace.
    This gives client-specific memories priority while still surfacing
    general technique learnings (email tips, pricing strategies, etc.).
    """
    import httpx

    async def _search_ns(user_id: str, n: int) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.post(
                    f"{config.memory.mem0_host}/v1/memories/search/",
                    json={"query": query, "user_id": user_id, "limit": n},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                return [r.get("memory", "") for r in results if r.get("memory")]
        except Exception as e:
            logger.debug(f"Mem0 search failed for {user_id} (non-critical): {e}")
            return []

    if client_id is not None:
        # Client-specific memories first, then backfill from system pool
        client_results = await _search_ns(f"client:{client_id}", limit)
        remaining = limit - len(client_results)
        if remaining > 0:
            system_results = await _search_ns("titan", remaining)
            return client_results + system_results
        return client_results
    else:
        return await _search_ns("titan", limit)


# ── Temporal Memory (Zep/Graphiti) — 3rd backend ─────────────────────
#
# Handles temporal fact expiry so stale memories stop poisoning prompts.
# Merge priority: Zep wins on timestamped facts, Qdrant wins on pure semantic.


async def store_temporal_fact(
    content: str,
    category: str,
    valid_days: int = 30,
    source_lead_id: int | None = None,
    metadata: dict | None = None,
    client_id: int | None = None,
) -> bool:
    """Store a fact with expiry. Facts auto-expire and stop appearing in searches.

    client_id scopes the fact to a per-client Zep session. System-level
    facts (no client_id) go to the "titan" session.

    Returns True if stored, False if Zep unavailable (graceful degradation).
    """
    if not config.memory.zep_enabled:
        return False

    from datetime import timedelta

    import httpx

    valid_until = (datetime.now() + timedelta(days=valid_days)).isoformat()
    fact_metadata = {
        "category": category,
        "valid_until": valid_until,
        "source_lead_id": source_lead_id,
        "created_at": datetime.now().isoformat(),
    }
    if metadata:
        fact_metadata.update(metadata)

    # Per-client Zep session, or "titan" for system-level facts
    zep_session = f"client_{client_id}" if client_id is not None else "titan"

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"{config.memory.zep_url}/api/v2/memory/{zep_session}/messages",
                json={
                    "messages": [{"role": "assistant", "content": content, "metadata": fact_metadata}],
                },
            )
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.debug(f"Zep store failed (non-critical): {e}")
        return False


async def search_temporal_facts(
    query: str, limit: int = 5, client_id: int | None = None,
) -> list[dict]:
    """Search temporal facts, auto-excluding expired ones.

    When client_id is provided, searches the client session first and
    backfills remaining slots from the system ("titan") session.

    Returns list of {"content": str, "valid_until": str, "category": str,
    "magma_node_id": str (optional)}.
    """
    if not config.memory.zep_enabled:
        return []

    import httpx

    async def _search_session(session_id: str, n: int) -> list:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.post(
                    f"{config.memory.zep_url}/api/v2/memory/{session_id}/search",
                    json={"text": query, "limit": n * 2},  # over-fetch then filter
                )
                resp.raise_for_status()
                return resp.json().get("results", [])
        except Exception as e:
            logger.debug(f"Zep search failed for session {session_id} (non-critical): {e}")
            return []

    if client_id is not None:
        raw_client = await _search_session(f"client_{client_id}", limit * 2)
        # Filter expired BEFORE deciding on backfill. Without this,
        # expired client hits inflate the count and suppress system
        # backfill, then get removed below — leaving too few results.
        now = datetime.now().isoformat()
        results = [
            r for r in raw_client
            if not (r.get("metadata", {}).get("valid_until", "") and r.get("metadata", {}).get("valid_until", "") < now)
        ]
        # Backfill from system session if live client results are sparse
        if len(results) < limit:
            system_results = await _search_session("titan", (limit - len(results)) * 2)
            results.extend(system_results)
    else:
        results = await _search_session("titan", limit * 2)

    # Filter out expired facts (system results may also be expired)
    now = datetime.now().isoformat()
    facts = []
    for r in results:
        meta = r.get("metadata", {})
        valid_until = meta.get("valid_until", "")
        if valid_until and valid_until < now:
            continue  # Expired — skip
        content = r.get("content", r.get("message", {}).get("content", ""))
        if content:
            fact = {
                "content": content,
                "valid_until": valid_until,
                "category": meta.get("category", ""),
            }
            # Preserve magma_node_id for RRF identity linkage —
            # without this, MAGMA falls back to fake zep_{rank} IDs
            # and cannot fuse Zep hits with their graph/vector twins.
            magma_nid = meta.get("magma_node_id")
            if magma_nid:
                fact["magma_node_id"] = magma_nid
            facts.append(fact)
        if len(facts) >= limit:
            break

    return facts


async def expire_stale_facts() -> int:
    """Mark facts past their valid_until as expired. Called by memory_gc()."""
    if not config.memory.zep_enabled:
        return 0
    # Zep handles expiry on read (we filter in search_temporal_facts).
    # This function exists for explicit cleanup if needed later.
    return 0


def _filter_stale_qdrant(vector_memories: list[str], temporal_facts: list[dict]) -> list[str]:
    """Remove Qdrant memories that Zep has expired.

    If Zep says a fact about topic X expired, don't inject Qdrant's
    stale version of that same fact. Prevents contradictory context.
    """
    if not temporal_facts:
        return vector_memories

    # Build a set of category keywords from expired/active Zep facts
    # so we can identify when Qdrant has a stale version of the same topic
    zep_topics: set[str] = set()
    for fact in temporal_facts:
        # Extract key phrases from Zep facts (first 5 significant words)
        words = fact.get("content", "").lower().split()[:10]
        zep_topics.update(w for w in words if len(w) > 4)

    # Only filter if there's enough Zep data to be meaningful
    if len(zep_topics) < 3:
        return vector_memories

    filtered = []
    for mem in vector_memories:
        mem_words = set(mem.lower().split())
        # If >40% of a memory's words overlap with Zep topics,
        # Zep's version is fresher — skip the Qdrant version
        overlap = len(mem_words & zep_topics) / max(len(mem_words), 1)
        if overlap < 0.4:
            filtered.append(mem)

    return filtered


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


async def get_relevant_learnings(
    context: str, limit: int = 10, client_id: int | None = None,
    query_type: str = "",
) -> str:
    """
    Get relevant learnings for a specific context.

    If MAGMA is enabled, routes through the intent-aware 4-graph retriever.
    Otherwise falls back to the 3-source stack (Zep + Postgres + Qdrant).

    client_id scopes retrieval to a single client's memories, preventing
    cross-client leakage across Mem0, MAGMA graph, and Postgres.

    query_type (e.g. "email_compose", "lead_research") selects ALMA
    meta-learned retrieval parameters when MAGMA is enabled.
    """
    # Try MAGMA first — intent-aware retrieval across all 4 graphs.
    # magma_retrieve returns "" when graph is unavailable (no driver,
    # no anchors, low confidence) so we fall through to the flat stack
    # instead of recursing back into this function.
    magma_prefix = ""
    if config.memory.magma_enabled:
        try:
            from shared.magma import magma_retrieve
            result = await magma_retrieve(context, limit=limit, client_id=client_id, query_type=query_type)
            if result and not result.startswith("[LOW CONFIDENCE"):
                return result
            if result and result.startswith("[LOW CONFIDENCE"):
                # Carry the warning, but still use flat-stack data below
                magma_prefix = result + "\n"
        except Exception as e:
            logger.debug(f"MAGMA retrieval failed, falling back to flat stack: {e}")

    # Fallback: 3-source stack (Zep + Postgres + Qdrant)
    # 1. Temporal facts from Zep (only non-expired, freshest data)
    temporal_facts = await search_temporal_facts(context, limit=5, client_id=client_id)

    # 2. Structured learnings from Postgres (high confidence first)
    #    Scoped to client_id when provided to prevent cross-client leakage.
    if client_id is not None:
        db_learnings = await fetch_all(
            """SELECT category, insight FROM titan_learnings
               WHERE confidence > 0.5 AND client_id = %s
               ORDER BY confidence DESC, created_at DESC LIMIT %s""",
            (client_id, limit),
        )
    else:
        db_learnings = await fetch_all(
            """SELECT category, insight FROM titan_learnings
               WHERE confidence > 0.5
               ORDER BY confidence DESC, created_at DESC LIMIT %s""",
            (limit,),
        )

    # 3. Vector search from Mem0 — filter stale entries against Zep
    raw_vector = await search_memory(context, limit=limit, client_id=client_id)
    vector_memories = _filter_stale_qdrant(raw_vector, temporal_facts)

    # Combine with merge priority: Zep > Postgres > Qdrant
    parts = []
    if temporal_facts:
        parts.append("CURRENT FACTS (verified fresh — trust these over older memories):")
        for f in temporal_facts:
            valid_str = f" [valid until {f.get('valid_until', 'indefinite')[:10]}]" if f.get("valid_until") else ""
            parts.append(f"  - {f['content']}{valid_str}")
    if db_learnings:
        parts.append("STRUCTURED INSIGHTS:")
        for learning in db_learnings:
            parts.append(f"  [{learning['category']}] {learning['insight']}")
    if vector_memories:
        parts.append("RELEVANT MEMORIES:")
        for m in vector_memories:
            parts.append(f"  - {m}")

    flat_result = "\n".join(parts) if parts else "No prior learnings yet."
    return magma_prefix + flat_result


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

    # Store in Postgres (with contradiction detection)
    for insight in insights:
        # Check for contradiction before storing
        try:
            contradiction = await _check_contradiction(insight)
        except Exception as e:
            logger.debug(f"Contradiction check failed (non-critical): {e}")
            contradiction = None

        if contradiction:
            resolution = contradiction.get("resolution", "debate")
            contradicted_id = contradiction.get("contradicted_id")
            logger.info(
                f"Contradiction detected: new='{insight.get('insight', '')[:60]}' "
                f"vs existing id={contradicted_id}, resolution={resolution}"
            )

            if resolution == "keep_old":
                # Skip inserting the new insight
                continue
            elif resolution == "merge" and contradiction.get("merge_text"):
                # Update existing with merged text, skip inserting new
                if contradicted_id:
                    await execute(
                        "UPDATE titan_learnings SET insight = %s, evaluated_at = NOW() WHERE id = %s",
                        (contradiction["merge_text"], contradicted_id),
                    )
                continue
            elif resolution == "debate":
                # Flag for Alpha/Beta to resolve in tonight's sleep cycle
                await emit_event("pending_contradiction", {
                    "type": "insight_conflict",
                    "new_insight": insight.get("insight", ""),
                    "new_category": insight.get("category", ""),
                    "contradicted_id": contradicted_id,
                    "action": "alpha_beta_debate",
                })
                continue
            # resolution == "keep_new" falls through to normal insert

            # If keep_new, deactivate the old one
            if resolution == "keep_new" and contradicted_id:
                await execute(
                    "DELETE FROM titan_learnings WHERE id = %s",
                    (contradicted_id,),
                )

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

    # Ingest into MAGMA graph (if enabled) — creates temporal + entity edges
    if config.memory.magma_enabled:
        try:
            from shared.magma import magma_ingest
            for insight in insights:
                await magma_ingest(
                    content=insight.get("insight", ""),
                    category=insight.get("category", "daily_reflection"),
                    metadata={"source": "daily_reflection", "confidence": insight.get("confidence", 0.5)},
                )
        except Exception as e:
            logger.debug(f"MAGMA ingest during reflection failed (non-critical): {e}")

    # Extract rules from metric changes (closed-loop learning)
    try:
        await extract_rules_from_reflection(metrics)
    except Exception as e:
        logger.warning(f"Rule extraction failed (non-critical): {e}")

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
{json.dumps([dict(learning) for learning in weekly_learnings], default=str)}

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

    # Evaluate existing rules — deactivate ones that aren't working
    try:
        await evaluate_rules()
    except Exception as e:
        logger.warning(f"Rule evaluation failed (non-critical): {e}")

    # 4.6: Analyze A/B test results — promote winners as rules
    try:
        ab_stats = await analyze_ab_results()
        if ab_stats.get("winners_promoted"):
            logger.info(f"A/B analysis promoted {ab_stats['winners_promoted']} winners")
    except Exception as e:
        logger.debug(f"A/B analysis failed (non-critical): {e}")


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

    # Source quality breakdown
    source_quality = await fetch_all(
        """SELECT source, total_leads, has_email, progressed, engaged, converted, paid,
                  revenue, total_cost, conversion_rate_pct
           FROM v_source_quality
           WHERE total_leads >= 5
           ORDER BY conversion_rate_pct DESC LIMIT 10"""
    )

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
        "source_quality": [dict(s) for s in source_quality] if source_quality else [],
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


# ── Closed-Loop Rules System ─────────────────────────────────────────
#
# Rules are data-proven behavioral constraints injected into pipeline prompts.
# Unlike learnings (suggestions the LLM can ignore), rules are deterministic:
# "Subject lines under 6 words get 2.1x open rate" → enforced in compose prompt.
# Rules are evaluated weekly; ones that don't hold up get deactivated.

async def get_active_rules(category: str = "") -> list[dict]:
    """Fetch active rules, optionally filtered by category."""
    if category:
        return await fetch_all(
            """SELECT id, category, rule_text, metric_name, metric_before, metric_after,
                      sample_size, confidence
               FROM titan_rules WHERE active = TRUE AND category = %s
               ORDER BY confidence DESC""",
            (category,),
        )
    return await fetch_all(
        """SELECT id, category, rule_text, metric_name, metric_before, metric_after,
                  sample_size, confidence
           FROM titan_rules WHERE active = TRUE
           ORDER BY confidence DESC"""
    )


async def format_rules_for_prompt(categories: list[str]) -> str:
    """Format active rules as numbered constraints for injection into LLM prompts."""
    rules = []
    for cat in categories:
        rules.extend(await get_active_rules(cat))

    if not rules:
        return ""

    lines = ["PROVEN RULES (follow these — they are data-backed, not suggestions):"]
    for i, rule in enumerate(rules, 1):
        metric_delta = ""
        if rule.get("metric_before") and rule.get("metric_after"):
            improvement = rule["metric_after"] / max(rule["metric_before"], 0.001)
            metric_delta = f" ({improvement:.1f}x improvement, n={rule.get('sample_size', 0)})"
        lines.append(f"  {i}. {rule['rule_text']}{metric_delta}")

    return "\n".join(lines)


async def extract_rules_from_reflection(metrics: dict, previous_metrics: dict | None = None):
    """
    After daily reflection, compare metrics to find improvements worth codifying as rules.
    Called by daily_reflection after insights are stored.
    """
    if not previous_metrics:
        # Get yesterday's metrics from the last daily reflection
        prev_learning = await fetch_one(
            """SELECT insight FROM titan_learnings
               WHERE category = 'daily_metrics'
               ORDER BY created_at DESC OFFSET 1 LIMIT 1"""
        )
        if prev_learning:
            try:
                previous_metrics = json.loads(prev_learning["insight"])
            except (json.JSONDecodeError, TypeError):
                return

    if not previous_metrics:
        return

    # Store today's metrics as a learning for tomorrow's comparison
    await execute(
        """INSERT INTO titan_learnings (category, insight, confidence)
           VALUES ('daily_metrics', %s, 1.0)""",
        (json.dumps(metrics, default=str),),
    )

    # Compare key metrics
    comparisons = [
        ("open_rate", "email_performance"),
        ("reply_rate", "email_performance"),
    ]

    for metric_name, rule_category in comparisons:
        current = metrics.get(metric_name, 0)
        previous = previous_metrics.get(metric_name, 0)
        sample = metrics.get("emails_sent", 0)

        if previous <= 0 or current <= 0 or sample < 50:
            continue

        improvement = current / previous
        if improvement >= 1.15:  # 15%+ improvement
            # Find what changed — check recent learnings for a cause
            recent = await fetch_all(
                """SELECT insight FROM titan_learnings
                   WHERE category IN ('email_performance', 'copywriting', 'targeting')
                   AND confidence >= 0.6
                   AND created_at > NOW() - INTERVAL '48 hours'
                   ORDER BY confidence DESC LIMIT 3"""
            )
            if not recent:
                continue

            # Ask LLM to extract a concrete rule from the improvement + insights
            insights_text = "; ".join(r["insight"] for r in recent)
            prompt = f"""{metric_name} improved from {previous:.1f}% to {current:.1f}% ({improvement:.1f}x) over {sample} emails.

Recent insights: {insights_text}

Extract ONE concrete, testable rule that likely caused this improvement.
The rule must be specific enough to enforce in a prompt (e.g. "Subject lines must be under 6 words" not "Write better subjects").

Return JSON: {{"rule_text": "...", "confidence": 0.0-1.0}}"""

            result = await llm.generate(prompt, model="fast", temperature=0.2)
            try:
                start = result.find("{")
                end = result.rfind("}") + 1
                rule_data = json.loads(result[start:end])
            except (json.JSONDecodeError, ValueError):
                continue

            rule_text = rule_data.get("rule_text", "").strip()
            if not rule_text:
                continue

            # Check for duplicate rules
            existing = await fetch_one(
                "SELECT id FROM titan_rules WHERE rule_text = %s AND active = TRUE",
                (rule_text,),
            )
            if existing:
                continue

            await execute(
                """INSERT INTO titan_rules
                   (category, rule_text, metric_name, metric_before, metric_after, sample_size, confidence, writer_agent)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    rule_category,
                    rule_text,
                    metric_name,
                    previous,
                    current,
                    sample,
                    rule_data.get("confidence", 0.6),
                    "titan.daily_reflection",
                ),
            )
            logger.info(f"New rule extracted: {rule_text} ({metric_name}: {previous:.1f}% → {current:.1f}%)")
            await emit_event("rule_created", {
                "rule_text": rule_text,
                "metric_name": metric_name,
                "improvement": f"{improvement:.1f}x",
            })


async def evaluate_rules():
    """
    Weekly evaluation: check if active rules still hold up.
    Deactivate rules whose metric has regressed since activation.
    """
    active_rules = await get_active_rules()
    if not active_rules:
        return

    logger.info(f"Evaluating {len(active_rules)} active rules...")

    for rule in active_rules:
        metric_name = rule.get("metric_name", "")
        metric_after = rule.get("metric_after", 0)

        # Get current metric value (last 7 days)
        current_value = 0
        if metric_name == "open_rate":
            row = await fetch_one(
                """SELECT CASE WHEN SUM(emails_sent) > 0
                          THEN ROUND(SUM(opens)::numeric / SUM(emails_sent) * 100, 1)
                          ELSE 0 END as rate
                   FROM outreach_metrics WHERE date > CURRENT_DATE - 7"""
            )
            current_value = float(row["rate"]) if row and row["rate"] else 0
        elif metric_name == "reply_rate":
            row = await fetch_one(
                """SELECT CASE WHEN SUM(emails_sent) > 0
                          THEN ROUND(SUM(replies)::numeric / SUM(emails_sent) * 100, 1)
                          ELSE 0 END as rate
                   FROM outreach_metrics WHERE date > CURRENT_DATE - 7"""
            )
            current_value = float(row["rate"]) if row and row["rate"] else 0

        if current_value <= 0:
            continue

        # If current metric is worse than when the rule was created, deactivate
        if metric_after > 0 and current_value < metric_after * 0.8:
            await execute(
                "UPDATE titan_rules SET active = FALSE, evaluated_at = NOW() WHERE id = %s",
                (rule["id"],),
            )
            logger.info(f"Deactivated rule #{rule['id']}: {rule['rule_text']} ({metric_name} regressed to {current_value}%)")
            await emit_event("rule_deactivated", {
                "rule_id": rule["id"],
                "rule_text": rule["rule_text"],
                "reason": f"{metric_name} regressed from {metric_after}% to {current_value}%",
            })
        else:
            # Update evaluation timestamp and sample size
            await execute(
                "UPDATE titan_rules SET evaluated_at = NOW() WHERE id = %s",
                (rule["id"],),
            )

    # ── Confidence decay: rules not validated in 30 days lose confidence ──
    stale_rules = await fetch_all(
        """SELECT id, confidence, rule_text FROM titan_rules
           WHERE active = TRUE
           AND (evaluated_at IS NULL OR evaluated_at < NOW() - INTERVAL '30 days')
           AND created_at < NOW() - INTERVAL '30 days'"""
    )
    for rule in stale_rules:
        old_conf = float(rule.get("confidence", 0.5))
        new_conf = round(max(0.1, old_conf - 0.1), 2)
        if new_conf <= 0.3:
            await execute(
                "UPDATE titan_rules SET active = FALSE, confidence = %s, evaluated_at = NOW() WHERE id = %s",
                (new_conf, rule["id"]),
            )
            logger.info(f"Confidence decay deactivated rule #{rule['id']}: '{rule['rule_text']}' (confidence {old_conf} → {new_conf})")
            await emit_event("rule_deactivated", {
                "rule_id": rule["id"],
                "rule_text": rule["rule_text"],
                "reason": f"confidence decayed from {old_conf} to {new_conf} (no validation in 30+ days)",
            })
        else:
            await execute(
                "UPDATE titan_rules SET confidence = %s, evaluated_at = NOW() WHERE id = %s",
                (new_conf, rule["id"]),
            )
            logger.info(f"Confidence decay: rule #{rule['id']} {old_conf} → {new_conf}")


# ── Prompt Version Hashing (5.1) ─────────────────────────────────────

def compute_prompt_version(soul_copy: str, rules: list[dict], ab_variation: str = "") -> str:
    """Hash the current prompt ingredients to trace which version produced which outcome.

    Stored on training_data and email_sequences so we can attribute metric
    changes to specific prompt edits, rule changes, or A/B variations.
    """
    rules_str = json.dumps(
        [{"rule_text": r.get("rule_text", ""), "id": r.get("id")} for r in rules],
        sort_keys=True,
    )
    content = f"{soul_copy}|{rules_str}|{ab_variation}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Memory Garbage Collection (1.2) ──────────────────────────────────

async def memory_gc() -> dict:
    """Nightly memory hygiene: prune stale learnings, dedup, clean contradicted rules.

    Called at the start of each sleep cycle to ensure the system debates
    with clean data, not accumulated noise.
    """
    stats = {"pruned": 0, "deduped": 0, "rules_flagged": 0}

    # 1. Prune low-confidence learnings older than 90 days
    try:
        result = await fetch_all(
            """DELETE FROM titan_learnings
               WHERE confidence < 0.4
               AND created_at < NOW() - INTERVAL '90 days'
               RETURNING id"""
        )
        stats["pruned"] = len(result) if result else 0
        if stats["pruned"]:
            logger.info(f"memory_gc: pruned {stats['pruned']} low-confidence old learnings")
    except Exception as e:
        logger.warning(f"memory_gc: prune failed: {e}")

    # 2. Deduplicate: find near-identical insights (same category, high text similarity)
    #    Requires pg_trgm extension. Keep the highest-confidence version.
    try:
        dupes = await fetch_all(
            """SELECT b.id as remove_id, a.id as keep_id,
                      b.insight as remove_insight, a.insight as keep_insight
               FROM titan_learnings a
               JOIN titan_learnings b ON a.category = b.category
                 AND a.id < b.id
                 AND a.confidence >= b.confidence
                 AND similarity(a.insight, b.insight) > 0.85
               WHERE b.created_at > NOW() - INTERVAL '90 days'
               LIMIT 50"""
        )
        if dupes:
            remove_ids = [d["remove_id"] for d in dupes]
            for rid in remove_ids:
                await execute("DELETE FROM titan_learnings WHERE id = %s", (rid,))
            stats["deduped"] = len(remove_ids)
            logger.info(f"memory_gc: deduped {stats['deduped']} near-identical learnings")
    except Exception as e:
        # pg_trgm might not be installed — degrade gracefully
        logger.debug(f"memory_gc: dedup skipped (pg_trgm may not be installed): {e}")

    # 3. Flag contradictory active rules for Alpha/Beta debate
    #    Rules with same metric_name but opposite direction (one improved, one regressed)
    try:
        contradictions = await fetch_all(
            """SELECT a.id as rule_a, b.id as rule_b,
                      a.rule_text as text_a, b.rule_text as text_b,
                      a.metric_name
               FROM titan_rules a
               JOIN titan_rules b ON a.metric_name = b.metric_name
                 AND a.id < b.id
                 AND a.active = TRUE AND b.active = TRUE
                 AND a.metric_after > a.metric_before
                 AND b.metric_after < b.metric_before"""
        )
        if contradictions:
            for c in contradictions:
                await emit_event("pending_contradiction", {
                    "type": "rule_conflict",
                    "rule_a_id": c["rule_a"],
                    "rule_a_text": c["text_a"],
                    "rule_b_id": c["rule_b"],
                    "rule_b_text": c["text_b"],
                    "metric": c["metric_name"],
                    "action": "alpha_beta_debate",
                })
            stats["rules_flagged"] = len(contradictions)
            logger.info(f"memory_gc: flagged {stats['rules_flagged']} contradictory rule pairs")
    except Exception as e:
        logger.debug(f"memory_gc: contradiction check failed: {e}")

    logger.info(f"memory_gc complete: {stats}")
    return stats


# ── Contradiction Detection (1.4) ────────────────────────────────────

async def _check_contradiction(new_insight: dict) -> dict | None:
    """Check if a new insight contradicts existing high-confidence learnings.

    Returns None if no contradiction, or a dict with resolution info.
    Called by daily_reflection before storing each insight.
    """
    category = new_insight.get("category", "")
    insight_text = new_insight.get("insight", "")

    existing = await fetch_all(
        """SELECT id, insight, confidence FROM titan_learnings
           WHERE category = %s AND confidence > 0.6
           AND created_at > NOW() - INTERVAL '30 days'
           ORDER BY confidence DESC LIMIT 5""",
        (category,),
    )
    if not existing:
        return None

    existing_text = "\n".join(
        f"- [id={e['id']}] {e['insight']} (confidence: {e['confidence']})"
        for e in existing
    )

    result = await llm.generate(
        f"New insight: {insight_text}\n\n"
        f"Existing insights in category '{category}':\n{existing_text}\n\n"
        f"Does the new insight DIRECTLY CONTRADICT any existing insight? "
        f"A contradiction means they cannot both be true at the same time.\n"
        f"Return JSON: {{\"contradicts\": true/false, \"contradicted_id\": <id or null>, "
        f"\"resolution\": \"keep_new|keep_old|merge|debate\", "
        f"\"merge_text\": \"merged insight if resolution is merge, else empty\"}}",
        model="fast",
        temperature=0.1,
    )

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        parsed = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        return None

    if not parsed.get("contradicts"):
        return None

    return parsed


# ── Cross-Agent Skill Performance (3.3) ──────────────────────────────

async def get_skill_success_rates(min_samples: int = 3) -> dict[str, dict]:
    """Query ClawdBot's skill performance learnings and compute success rates.

    Returns {task_type: {"success": int, "failure": int, "total": int, "rate": float}}
    Used by Titan's expansion system to prefer high-success skills.
    """
    learnings = await fetch_all(
        """SELECT insight, confidence FROM titan_learnings
           WHERE category = 'skill_performance'
           AND created_at > NOW() - INTERVAL '30 days'"""
    )

    rates: dict[str, dict] = {}
    for l in learnings:
        insight = l.get("insight", "")
        _confidence = float(l.get("confidence", 0.5))

        # Parse task type from insight text
        # Format: "Task 'task_type' succeeded: ..." or "Task 'task_type' failed: ..."
        if "' succeeded" in insight:
            start = insight.find("'") + 1
            end = insight.find("'", start)
            task_type = insight[start:end] if start > 0 and end > start else ""
            if task_type:
                rates.setdefault(task_type, {"success": 0, "failure": 0, "total": 0, "rate": 0.0})
                rates[task_type]["success"] += 1
                rates[task_type]["total"] += 1
        elif "' failed" in insight:
            start = insight.find("'") + 1
            end = insight.find("'", start)
            task_type = insight[start:end] if start > 0 and end > start else ""
            if task_type:
                rates.setdefault(task_type, {"success": 0, "failure": 0, "total": 0, "rate": 0.0})
                rates[task_type]["failure"] += 1
                rates[task_type]["total"] += 1

    # Compute rates, filter by min samples
    result = {}
    for task_type, data in rates.items():
        if data["total"] >= min_samples:
            data["rate"] = round(data["success"] / data["total"], 3)
            result[task_type] = data

    return result


# ── GraphRAG Consolidation (3.2, moved to Phase 2) ──────────────────

async def graphrag_consolidation() -> dict:
    """Weekly: cluster Mem0 entries by category, consolidate into high-signal nodes.

    Reduces context noise: 400 raw memories → 20 high-signal summaries.
    Consolidates BOTH the shared "titan" namespace AND per-client namespaces,
    so client-specific memory growth is also managed.
    Must run BEFORE prospect state and re-enrichment inject into prompts.
    """

    stats = {"categories_consolidated": 0, "memories_consumed": 0, "summaries_created": 0}

    # Collect all user_ids to consolidate: shared "titan" + active client namespaces
    user_ids = ["titan"]
    try:
        client_rows = await fetch_all(
            "SELECT id FROM clients WHERE status NOT IN ('lost', 'unsubscribed') LIMIT 100"
        )
        for row in (client_rows or []):
            user_ids.append(f"client:{row['id']}")
    except Exception as e:
        logger.debug(f"graphrag_consolidation: could not list client namespaces: {e}")

    for user_id in user_ids:
        ns_stats = await _consolidate_namespace(user_id)
        stats["categories_consolidated"] += ns_stats["categories_consolidated"]
        stats["memories_consumed"] += ns_stats["memories_consumed"]
        stats["summaries_created"] += ns_stats["summaries_created"]

    logger.info(f"graphrag_consolidation complete ({len(user_ids)} namespaces): {stats}")
    await emit_event("graphrag_consolidation_complete", stats)
    return stats


async def _consolidate_namespace(user_id: str) -> dict:
    """Consolidate Mem0 memories for a single user_id namespace."""
    import httpx

    stats = {"categories_consolidated": 0, "memories_consumed": 0, "summaries_created": 0}

    # 1. Fetch recent memories grouped by category from Mem0
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{config.memory.mem0_host}/v1/memories/search/",
                json={"query": "all recent learnings", "user_id": user_id, "limit": 200},
            )
            resp.raise_for_status()
            all_memories = resp.json().get("results", [])
    except Exception as e:
        logger.debug(f"graphrag_consolidation[{user_id}]: could not fetch memories: {e}")
        return stats

    if len(all_memories) < 10:
        return stats

    # 2. Group by category
    groups: dict[str, list[dict]] = {}
    for mem in all_memories:
        cat = mem.get("metadata", {}).get("category", "uncategorized")
        groups.setdefault(cat, []).append(mem)

    # 3. Consolidate groups with 5+ entries
    for category, memories in groups.items():
        if len(memories) < 5:
            continue

        memory_texts = [m.get("memory", "") for m in memories if m.get("memory")]
        if len(memory_texts) < 5:
            continue

        # Ask LLM to consolidate
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(memory_texts[:30]))
        try:
            result = await llm.generate(
                f"You have {len(memory_texts)} memories in category '{category}':\n\n"
                f"{numbered}\n\n"
                f"Consolidate these into 1-3 HIGH-SIGNAL summary insights. "
                f"Each summary should capture the strongest pattern across multiple memories. "
                f"Drop noise, keep what's statistically supported.\n\n"
                f"Return JSON list: [{{\"insight\": \"...\", \"confidence\": 0.0-1.0, "
                f"\"based_on_count\": <number of original memories this summarizes>}}]",
                model="fast",
                temperature=0.2,
            )

            start = result.find("[")
            end = result.rfind("]") + 1
            summaries = json.loads(result[start:end])
        except Exception as e:
            logger.debug(f"graphrag_consolidation: LLM consolidation failed for {category}: {e}")
            continue

        # 4. Store consolidated summaries with high importance
        for summary in summaries[:3]:
            insight = summary.get("insight", "")
            confidence = min(1.0, max(0.3, summary.get("confidence", 0.7)))
            based_on = summary.get("based_on_count", len(memory_texts))
            if not insight:
                continue

            # Store as high-importance consolidated memory.
            # Extract client_id from user_id for client namespaces.
            consolidation_client_id = None
            if user_id.startswith("client:"):
                try:
                    consolidation_client_id = int(user_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    pass
            await store_memory(
                f"[CONSOLIDATED from {based_on} memories] {insight}",
                category=f"{category}_consolidated",
                client_id=consolidation_client_id,
                metadata={
                    "consolidated": True,
                    "based_on_count": based_on,
                    "source_category": category,
                },
                outcome_magnitude=confidence,
                sample_size=based_on,
            )
            stats["summaries_created"] += 1

        stats["categories_consolidated"] += 1
        stats["memories_consumed"] += len(memory_texts)

        # 5. Delete individual memories that were consolidated (via Mem0 API)
        for mem in memories:
            mem_id = mem.get("id")
            if mem_id:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.delete(
                            f"{config.memory.mem0_host}/v1/memories/{mem_id}/",
                            params={"user_id": user_id},
                        )
                except Exception:
                    pass  # Best-effort cleanup

    return stats


# ── Memory Importance Score (2.3) ────────────────────────────────────

def _compute_importance(outcome_magnitude: float, sample_size: int) -> float:
    """Bayesian-flavored importance: weight by evidence strength.

    outcome_magnitude: 0.0-1.0 (how impactful the insight is)
    sample_size: number of observations supporting it (saturates at n=100)
    """
    evidence_weight = min(1.0, sample_size / 100)
    return round(outcome_magnitude * evidence_weight, 3)


# ── Outcome Delay Tracking (2.1) ────────────────────────────────────

async def check_pending_outcomes() -> dict:
    """Re-check lead status for pending training outcomes at 7/14/30 day marks.

    Called hourly. Updates training_data with accurate delayed outcomes
    so the LoRA model trains on reality, not premature labels.
    """
    stats = {"checked": 0, "updated": 0, "silence_attributed": 0}

    pending = await fetch_all(
        """SELECT po.id, po.training_data_id, po.client_id, po.email_seq_id, po.check_type
           FROM pending_outcomes po
           WHERE po.check_at <= NOW() AND po.checked = FALSE
           ORDER BY po.check_at ASC LIMIT 100"""
    )

    for p in pending:
        stats["checked"] += 1
        client = await fetch_one(
            "SELECT status FROM clients WHERE id = %s", (p["client_id"],)
        )
        if not client:
            await execute("UPDATE pending_outcomes SET checked = TRUE WHERE id = %s", (p["id"],))
            continue

        status = client["status"]
        # Map current lead status to training outcome
        status_to_outcome = {
            "interested": "positive",
            "demo_built": "positive",
            "proposal_sent": "positive",
            "negotiating": "positive",
            "closed": "positive",
            "building": "positive",
            "deployed": "positive",
            "invoiced": "positive",
            "paid": "positive",
            "lost": "negative",
            "unsubscribed": "negative",
        }
        new_outcome = status_to_outcome.get(status, "")

        if new_outcome and p["training_data_id"]:
            # Check if the outcome actually changed
            existing = await fetch_one(
                "SELECT outcome FROM training_data WHERE id = %s", (p["training_data_id"],)
            )
            if existing and existing["outcome"] != new_outcome:
                await execute(
                    "UPDATE training_data SET outcome = %s WHERE id = %s",
                    (new_outcome, p["training_data_id"]),
                )
                stats["updated"] += 1
                logger.info(
                    f"Delayed outcome updated: training_data {p['training_data_id']} "
                    f"→ {new_outcome} (was: {existing['outcome']}, check: {p['check_type']})"
                )

        # At 14-day mark, attribute silence for emails with zero engagement
        if p["check_type"] == "14day" and status in ("email_sent", "followed_up", "email_drafted", "email_queued"):
            try:
                await _attribute_silence(p["client_id"], p["email_seq_id"])
                stats["silence_attributed"] += 1
            except Exception as e:
                logger.debug(f"Silence attribution failed for client {p['client_id']}: {e}")

        await execute("UPDATE pending_outcomes SET checked = TRUE WHERE id = %s", (p["id"],))

    if stats["updated"] or stats["silence_attributed"]:
        logger.info(f"check_pending_outcomes: {stats}")
    return stats


async def create_pending_outcome_checks(training_data_id: int, client_id: int, email_seq_id: int | None = None):
    """Schedule 7/14/30-day outcome re-checks for a training example."""
    for check_type, days in [("7day", 7), ("14day", 14), ("30day", 30)]:
        await execute(
            """INSERT INTO pending_outcomes (training_data_id, client_id, email_seq_id, check_at, check_type)
               VALUES (%s, %s, %s, NOW() + INTERVAL '%s days', %s)""",
            (training_data_id, client_id, email_seq_id, days, check_type),
        )


# ── Causal Credit Assignment (2.2) ──────────────────────────────────

async def attribute_reply_cause(original_email: str, reply_body: str, outcome: str, client_id: int | None = None):
    """Analyze which sentence in the original email caused the reply.

    Works for both positive (what hooked them) and negative (what killed it).
    The contrast between the two is where the real signal lives.
    """
    if outcome == "positive":
        prompt = (
            f"Original cold email:\n{original_email}\n\n"
            f"Prospect's positive reply:\n{reply_body}\n\n"
            f"Which specific sentence or element in the original email most likely "
            f"triggered this positive response? Consider: the hook, the value prop, "
            f"the social proof, the CTA, personalization, timing reference.\n\n"
            f"Return JSON: {{\"trigger_sentence\": \"...\", \"trigger_type\": "
            f"\"hook|value_prop|social_proof|cta|personalization|timing\", "
            f"\"effect\": \"positive\", \"confidence\": 0.0-1.0}}"
        )
    elif outcome == "negative":
        prompt = (
            f"Original cold email:\n{original_email}\n\n"
            f"Prospect's negative reply:\n{reply_body}\n\n"
            f"Which specific sentence or element most likely caused this negative "
            f"reaction? Look for: generic claims, pushy CTA, wrong tone, irrelevant "
            f"value prop, spam-sounding phrases, factual errors about their business.\n\n"
            f"Return JSON: {{\"trigger_sentence\": \"...\", \"trigger_type\": "
            f"\"generic_claim|pushy_cta|wrong_tone|irrelevant_offer|spam_phrase|factual_error\", "
            f"\"effect\": \"negative\", \"confidence\": 0.0-1.0}}"
        )
    else:
        return

    try:
        result = await llm.generate(prompt, model="fast", temperature=0.2)
        start = result.find("{")
        end = result.rfind("}") + 1
        attribution = json.loads(result[start:end])
    except Exception as e:
        logger.debug(f"Causal attribution failed: {e}")
        return

    trigger = attribution.get("trigger_sentence", "")
    trigger_type = attribution.get("trigger_type", "unknown")
    effect = attribution.get("effect", outcome)
    confidence = min(1.0, max(0.1, attribution.get("confidence", 0.5)))

    if not trigger:
        return

    # Store as structured learning with proper client_id column
    await execute(
        """INSERT INTO titan_learnings (category, insight, confidence, client_id)
           VALUES ('sentence_attribution', %s, %s, %s)""",
        (
            json.dumps({
                "trigger_sentence": trigger[:200],
                "trigger_type": trigger_type,
                "effect": effect,
            }),
            confidence,
            client_id,
        ),
    )

    # Also store as training example for future fine-tuning
    from titan.training import collect_training_example
    await collect_training_example(
        example_type="attribution",
        input_text=original_email[:1000],
        output_text=json.dumps({"trigger": trigger[:200], "type": trigger_type, "effect": effect}),
        outcome=outcome,
        metadata={"client_id": client_id, "trigger_type": trigger_type},
    )

    logger.info(f"Causal attribution ({effect}): type={trigger_type}, confidence={confidence:.2f}")


async def _attribute_silence(client_id: int, email_seq_id: int | None = None):
    """Analyze emails that got zero engagement — what pattern killed them?

    Called by check_pending_outcomes at the 14-day mark for silent leads.
    """
    # Find the original email
    if email_seq_id:
        email = await fetch_one(
            "SELECT subject, body FROM email_sequences WHERE id = %s", (email_seq_id,)
        )
    else:
        email = await fetch_one(
            """SELECT subject, body FROM email_sequences
               WHERE client_id = %s ORDER BY created_at ASC LIMIT 1""",
            (client_id,),
        )

    if not email or not email.get("body"):
        return

    original = f"Subject: {email.get('subject', '')}\n\n{email['body']}"

    try:
        result = await llm.generate(
            f"This cold email got zero opens or replies after 14 days:\n\n"
            f"{original}\n\n"
            f"Identify the most likely reason it failed. Focus on: subject line, "
            f"first sentence, length, specificity, spam signals.\n\n"
            f"Return JSON: {{\"likely_cause\": \"...\", \"trigger_type\": "
            f"\"weak_subject|bad_opening|too_long|too_generic|spam_trigger|wrong_audience\", "
            f"\"effect\": \"silence\", \"confidence\": 0.0-1.0}}",
            model="fast",
            temperature=0.2,
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        attribution = json.loads(result[start:end])
    except Exception as e:
        logger.debug(f"Silence attribution failed: {e}")
        return

    cause = attribution.get("likely_cause", "")
    trigger_type = attribution.get("trigger_type", "unknown")
    confidence = min(1.0, max(0.1, attribution.get("confidence", 0.5)))

    if not cause:
        return

    await execute(
        """INSERT INTO titan_learnings (category, insight, confidence, client_id)
           VALUES ('sentence_attribution', %s, %s, %s)""",
        (
            json.dumps({
                "likely_cause": cause[:200],
                "trigger_type": trigger_type,
                "effect": "silence",
            }),
            confidence,
            client_id,
        ),
    )
    logger.info(f"Silence attribution: type={trigger_type}, cause={cause[:60]}")


# ── Lead Re-Enrichment (4.3) ────────────────────────────────────────

async def re_enrich_active_leads() -> dict:
    """Re-research active leads every 48h so emails hit a live nerve, not stale data.

    Scrapes fresh Google reviews, social media, recent news. If significant
    new info found, updates research_facts and stores as temporal fact in Zep.
    """
    stats = {"checked": 0, "enriched": 0, "alerts": 0}

    stale_leads = await fetch_all(
        """SELECT id, business_name, website, email, research_summary
           FROM clients
           WHERE status IN ('interested', 'demo_built', 'proposal_sent', 'negotiating')
           AND (enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '48 hours')
           LIMIT 10"""
    )

    for lead in stale_leads:
        stats["checked"] += 1
        lead_id = lead["id"]
        business = lead.get("business_name", "")

        # Try to scrape fresh info via firecrawl
        try:
            from tools.firecrawl_client import enrich_business_profile
            fresh = enrich_business_profile(business, website_url=str(lead.get("website", "")))
            if not fresh or not fresh.get("available"):
                continue
        except Exception as e:
            logger.debug(f"Re-enrichment scrape failed for {business}: {e}")
            continue

        fresh_summary = fresh.get("data", {}).get("summary", "")
        if not fresh_summary:
            continue

        # Compare with existing research — is there new info?
        old_summary = lead.get("research_summary", "") or ""
        if len(fresh_summary) < 20:
            continue

        # Ask LLM: is there anything genuinely new?
        try:
            diff_result = await llm.generate(
                f"Old research on {business}:\n{old_summary[:500]}\n\n"
                f"Fresh research:\n{fresh_summary[:500]}\n\n"
                f"Is there genuinely NEW information (new reviews, complaints, "
                f"events, hiring, promotions, pricing changes) not in the old research?\n"
                f"Return JSON: {{\"has_new_info\": true/false, \"new_facts\": \"...\"}}",
                model="fast", temperature=0.1,
            )
            start = diff_result.find("{")
            end = diff_result.rfind("}") + 1
            diff = json.loads(diff_result[start:end])
        except Exception:
            continue

        if diff.get("has_new_info") and diff.get("new_facts"):
            new_facts = diff["new_facts"]

            # Update research_facts and enriched_at
            await execute(
                "UPDATE clients SET enriched_at = NOW() WHERE id = %s",
                (lead_id,),
            )

            # Store as temporal fact (valid 7 days) so prompts get the fresh data
            await store_temporal_fact(
                f"Fresh intel on {business}: {new_facts[:300]}",
                category="lead_re_enrichment",
                valid_days=7,
                source_lead_id=lead_id,
                client_id=lead_id,
            )

            stats["enriched"] += 1
            logger.info(f"Re-enriched {business}: {new_facts[:60]}")
        else:
            # Nothing new — just update timestamp
            await execute(
                "UPDATE clients SET enriched_at = NOW() WHERE id = %s",
                (lead_id,),
            )

    if stats["enriched"]:
        logger.info(f"re_enrich_active_leads: {stats}")
    return stats


# ── Prospect Emotional State (4.4) ──────────────────────────────────

async def update_prospect_state(client_id: int, event_type: str, data: dict) -> None:
    """Track running psychological profile per lead.

    Accumulates: contact count, reply tones, open-to-reply timing, engagement.
    Injected into compose/follow-up prompts so Titan adapts its approach.
    Fires Hermes alert when state is ambiguous to prevent wrong moves.
    """
    current = await fetch_val(
        "SELECT prospect_state FROM clients WHERE id = %s", (client_id,)
    )
    if isinstance(current, str):
        try:
            current = json.loads(current)
        except (json.JSONDecodeError, TypeError):
            current = {}
    current = current or {}

    if event_type == "email_sent":
        current["contact_count"] = current.get("contact_count", 0) + 1
        current["last_contact"] = data.get("timestamp", datetime.now().isoformat())

    elif event_type == "email_opened":
        current["opens_without_reply"] = current.get("opens_without_reply", 0) + 1

    elif event_type == "reply_received":
        # Reset opens counter on reply
        current["opens_without_reply"] = 0
        body = data.get("body", "")[:500]

        if body:
            try:
                tone = await llm.generate(
                    f"Classify the emotional tone of this business reply in 1-2 words "
                    f"(e.g. 'interested', 'skeptical', 'annoyed', 'friendly', 'neutral', 'hostile'): "
                    f"{body}",
                    model="fast", temperature=0.1,
                )
                tone_label = tone.strip()[:30].lower()
                current.setdefault("tone_history", []).append(tone_label)
                # Keep last 10 tones
                current["tone_history"] = current["tone_history"][-10:]
            except Exception:
                pass

        if data.get("hours_since_last_contact"):
            current["reply_speed_hours"] = data["hours_since_last_contact"]

    # Check for ambiguous state → Hermes escalation
    if _is_ambiguous_state(current):
        current["low_confidence_state"] = True
        lead_name = data.get("business_name", f"client #{client_id}")
        await emit_event("urgent_alert", {
            "sender": "titan.prospect_state",
            "message": (
                f"Ambiguous prospect state for {lead_name} — "
                f"opens: {current.get('opens_without_reply', 0)}, "
                f"contacts: {current.get('contact_count', 0)}, "
                f"tones: {current.get('tone_history', [])}. "
                f"Review before next touchpoint."
            ),
        })
    else:
        current["low_confidence_state"] = False

    await execute(
        "UPDATE clients SET prospect_state = %s WHERE id = %s",
        (json.dumps(current), client_id),
    )


def _is_ambiguous_state(state: dict) -> bool:
    """Flag states where automated follow-up could backfire."""
    # 5+ opens with no reply = interested but hesitant, don't push
    if state.get("opens_without_reply", 0) >= 5:
        return True
    # Mixed tone history = confused, needs human eye
    tones = state.get("tone_history", [])
    if len(tones) >= 2:
        has_neg = any(t in ("hostile", "annoyed", "angry", "negative") for t in tones)
        has_pos = any(t in ("interested", "friendly", "positive", "enthusiastic") for t in tones)
        if has_neg and has_pos:
            return True
    return False


async def get_prospect_state(client_id: int) -> dict:
    """Read prospect state for prompt injection."""
    raw = await fetch_val(
        "SELECT prospect_state FROM clients WHERE id = %s", (client_id,)
    )
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return raw or {}


# ── A/B Testing Primitive (4.6) ─────────────────────────────────────

AB_MIN_SAMPLE_PER_VARIANT = 30  # hard floor — no evaluation below this

# Rotate through these variations one at a time
_AB_VARIATIONS = [
    "subject_length",           # short (<6 words) vs normal
    "cta_style",                # question CTA vs direct CTA
    "tone",                     # casual/conversational vs professional
    "personalization_depth",    # surface (name+city) vs deep (specific research)
]


async def pick_next_ab_variation() -> str:
    """Pick the next A/B variation to test, rotating through the list."""
    last_var = await fetch_val(
        """SELECT ab_variation FROM email_sequences
           WHERE ab_cohort IS NOT NULL
           ORDER BY created_at DESC LIMIT 1"""
    )
    if last_var and last_var in _AB_VARIATIONS:
        idx = (_AB_VARIATIONS.index(last_var) + 1) % len(_AB_VARIATIONS)
    else:
        idx = 0
    return _AB_VARIATIONS[idx]


async def analyze_ab_results() -> dict:
    """Weekly: evaluate A/B test results with statistical rigor.

    Minimum n≥30 per variant before evaluation. Chi-squared test.
    Winners become titan_rules with high confidence.
    """
    stats = {"tests_evaluated": 0, "winners_promoted": 0, "insufficient_data": 0}

    # Get all active A/B variations with enough data
    cohorts = await fetch_all(
        """SELECT ab_variation, ab_cohort,
                  COUNT(*) as n,
                  COUNT(*) FILTER (WHERE outcome = 'positive') as successes
           FROM training_data
           WHERE ab_cohort IS NOT NULL
           AND created_at > NOW() - INTERVAL '14 days'
           GROUP BY ab_variation, ab_cohort"""
    )

    if not cohorts:
        return stats

    # Pair A/B cohorts by variation
    pairs: dict[str, dict] = {}
    for c in cohorts:
        var = c["ab_variation"]
        cohort = c["ab_cohort"]
        pairs.setdefault(var, {})[cohort] = {"n": c["n"], "successes": c["successes"]}

    for variation, data in pairs.items():
        if "a" not in data or "b" not in data:
            continue

        a = data["a"]
        b = data["b"]

        # Minimum sample gate — skip if either variant has n < 30
        if a["n"] < AB_MIN_SAMPLE_PER_VARIANT or b["n"] < AB_MIN_SAMPLE_PER_VARIANT:
            stats["insufficient_data"] += 1
            logger.info(
                f"A/B test '{variation}': needs more data — "
                f"A={a['n']}, B={b['n']} (need {AB_MIN_SAMPLE_PER_VARIANT} each)"
            )
            continue

        stats["tests_evaluated"] += 1

        # Simple proportions test (z-test approximation)
        rate_a = a["successes"] / a["n"] if a["n"] else 0
        rate_b = b["successes"] / b["n"] if b["n"] else 0

        # Need meaningful difference (>20% relative improvement)
        if rate_a == 0 and rate_b == 0:
            continue

        if rate_a > rate_b and rate_a > 0:
            improvement = rate_a / max(rate_b, 0.001)
            winner = "a"
        elif rate_b > rate_a and rate_b > 0:
            improvement = rate_b / max(rate_a, 0.001)
            winner = "b"
        else:
            continue

        if improvement < 1.2:
            continue  # Less than 20% improvement — not meaningful

        # Promote winner as a titan_rule
        await execute(
            """INSERT INTO titan_rules
               (category, rule_text, metric_name, metric_before, metric_after,
                sample_size, confidence, writer_agent)
               VALUES ('ab_test', %s, %s, %s, %s, %s, %s, %s)""",
            (
                f"A/B test '{variation}': variant {winner} outperforms "
                f"({improvement:.1f}x, n={a['n']+b['n']})",
                f"ab_{variation}_reply_rate",
                round(min(rate_a, rate_b) * 100, 1),
                round(max(rate_a, rate_b) * 100, 1),
                a["n"] + b["n"],
                min(0.9, 0.6 + (a["n"] + b["n"]) / 200),  # confidence scales with sample
                "titan.ab_test",
            ),
        )
        stats["winners_promoted"] += 1
        logger.info(
            f"A/B winner: '{variation}' variant {winner} "
            f"({improvement:.1f}x, a={rate_a:.2%}, b={rate_b:.2%})"
        )

    if stats["tests_evaluated"]:
        await emit_event("ab_test_results", stats)
    return stats


# ── Competitor Intelligence (4.7) ───────────────────────────────────

COMPETITIVE_PRICE_UNDERCUT_THRESHOLD = 249  # alert if competitor drops below this


async def process_competitive_finding(finding: dict) -> None:
    """Process a scout finding about competitors. Store + react.

    Immediate alerts for price undercuts and new AI builders.
    Everything else feeds weekly strategy review.
    """
    category = finding.get("category", "")

    # 1. Always store the intel
    await execute(
        """INSERT INTO titan_learnings (category, insight, confidence)
           VALUES ('competitive_intel', %s, %s)""",
        (json.dumps(finding, default=str), finding.get("confidence", 0.5)),
    )

    # 2. Store in vector memory for strategy review retrieval
    await store_memory(
        f"Competitive intel: {json.dumps(finding, default=str)[:300]}",
        category="competitive_intel",
        outcome_magnitude=0.7,
        sample_size=1,
    )

    # 3. Reactive triggers — don't wait for weekly review
    if category == "pricing":
        competitor_price = finding.get("price", 999)
        if isinstance(competitor_price, str):
            try:
                competitor_price = float(competitor_price.replace("$", "").strip())
            except ValueError:
                competitor_price = 999

        if competitor_price < COMPETITIVE_PRICE_UNDERCUT_THRESHOLD:
            await emit_event("urgent_alert", {
                "sender": "scout.competitive_intel",
                "message": (
                    f"Competitor '{finding.get('name', '?')}' dropped price to "
                    f"${competitor_price}. Our current: $299. "
                    f"Flag for Alpha/Beta pricing debate tonight."
                ),
            })
            await emit_event("pending_contradiction", {
                "type": "pricing_pressure",
                "source": "competitive_intel",
                "data": finding,
                "action": "alpha_beta_debate",
            })

    if category == "feature" and finding.get("feature_type") == "ai_site_builder":
        await emit_event("agent_recommendation", {
            "from_agent": "scout",
            "to_agent": "clawdbot",
            "topic": "competitive_capability",
            "message": (
                f"Competitor '{finding.get('name', '?')}' offers: "
                f"{finding.get('description', '?')[:200]}. "
                f"Evaluate if we should integrate or counter."
            ),
        })

    logger.info(f"Competitive finding processed: {category} — {finding.get('name', '?')}")
