"""Scout — Autonomous external intelligence gathering.

Searches 20+ sources (Product Hunt, HN, GitHub, Hugging Face, Reddit, X, npm,
PyPI, etc.) for new tools, techniques, competitors, and models relevant to each
pipeline stage. Evaluates findings with LLM, stores actionable ones as
learnings/recommendations, and dispatches high-value tool discoveries to
ClawdBot's capability resolver.

Runs 2x daily from the orchestrator: 8 AM (Tier 1 + Tier 2) and 6 PM (Tier 1 only).
"""

import hashlib
import json
import logging
import time
from typing import Any

from shared.config import config
from shared.db import emit_event, get_config, set_config
from shared.llm_client import llm

logger = logging.getLogger("perseus.scout")

# ── Search Topics Per Pipeline Stage ──────────────────────────────────

SCOUT_TOPICS: dict[str, list[str]] = {
    "lead_discovery": [
        "AI lead generation tool",
        "business scraping API",
        "local business database API",
        "Apollo alternative 2026",
    ],
    "email_compose": [
        "cold email AI tool",
        "AI copywriting personalization",
        "email subject line optimization",
        "cold email template high reply rate",
    ],
    "site_building": [
        "AI website builder API",
        "Three.js template library",
        "design system component library",
        "AI web design tool programmatic",
        "GSAP animation templates",
    ],
    "deliverability": [
        "email warmup tool API",
        "email deliverability checker",
        "cold email deliverability tips 2026",
    ],
    "payments": [
        "invoice API small business",
        "payment link tool API",
        "crypto payment processor",
    ],
    "local_models": [
        "small language model local inference",
        "Ollama new model",
        "MLX fine-tuning Mac",
        "Hugging Face trending model",
    ],
    "general_tools": [
        "AI agent framework new",
        "MCP server new tool",
        "automation API launch 2026",
        "AI coding tool API",
    ],
}

# Tier 1: search-based sources (site: prefix queries via Firecrawl)
TIER1_SOURCES = [
    "site:producthunt.com",
    "site:news.ycombinator.com",
    "site:reddit.com",
    "site:x.com",
    "site:github.com",
    "site:huggingface.co",
    "site:dev.to",
    "site:medium.com",
    "site:youtube.com",
    "site:indiehackers.com",
    "site:futurepedia.io",
    "site:theresanaiforthat.com",
    "site:bensbites.com",
    "site:stackoverflow.com",
    "site:npmjs.com",
    "site:pypi.org",
    "site:vercel.com/blog",
    "site:anthropic.com/research",
    "site:replicate.com",
    "site:github.com awesome-",
]

# Tier 2: direct scrape URLs (trending pages, updated daily)
TIER2_URLS = [
    "https://github.com/trending?since=daily",
    "https://github.com/trending/python?since=daily",
    "https://github.com/trending/typescript?since=daily",
    "https://huggingface.co/models?sort=trending",
    "https://huggingface.co/spaces?sort=trending",
    "https://www.producthunt.com/",
    "https://replicate.com/explore",
    "https://www.npmjs.com/search?ranking=popularity&q=keywords:ai",
    "https://pypi.org/search/?q=ai+agent&o=-created",
    "https://ollama.com/library",
]

MAX_SEEN_URLS = 2000
SEEN_URL_TTL_DAYS = 60
MAX_TOPICS_PER_CYCLE = 5


# ── Core ──────────────────────────────────────────────────────────────


async def run_scout_cycle(*, include_tier2: bool = True) -> dict[str, Any]:
    """Run one scout cycle: select topics → search → dedup → evaluate → store → act.

    Args:
        include_tier2: If True, also scrape Tier 2 trending sources (morning cycle).
                       Set False for evening cycle.
    """
    try:
        topics = await _select_topics_for_cycle(SCOUT_TOPICS, max_topics=MAX_TOPICS_PER_CYCLE)
        logger.info("Scout cycle: searching %d topics across %d sources", len(topics), len(TIER1_SOURCES))

        # Phase 1: Search
        raw_results = await _search_sources(topics)
        if include_tier2:
            tier2_results = await _scrape_trending_sources()
            raw_results.extend(tier2_results)

        if not raw_results:
            logger.info("Scout cycle: no results found")
            return {"total": 0, "new": 0, "relevant": 0, "actionable": 0}

        # Phase 2: Dedup
        new_results = await _dedup_urls(raw_results)
        logger.info("Scout cycle: %d raw → %d new (after dedup)", len(raw_results), len(new_results))

        if not new_results:
            return {"total": len(raw_results), "new": 0, "relevant": 0, "actionable": 0}

        # Phase 3: Evaluate
        evaluated = await _evaluate_findings(new_results)
        relevant = [f for f in evaluated if f.get("relevant")]
        logger.info("Scout cycle: %d evaluated → %d relevant", len(evaluated), len(relevant))

        # Phase 4: Store and act
        counts = await _store_and_act(relevant)

        summary = {
            "total": len(raw_results),
            "new": len(new_results),
            "relevant": len(relevant),
            **counts,
        }

        await emit_event("scout_cycle_completed", summary)
        logger.info("Scout cycle complete: %s", json.dumps(summary))
        return summary

    except Exception as e:
        logger.warning("Scout cycle failed: %s", e)
        await emit_event("scout_cycle_failed", {"error": str(e)[:300]})
        return {"total": 0, "new": 0, "relevant": 0, "actionable": 0, "error": str(e)[:300]}


# ── Topic Selection ───────────────────────────────────────────────────


async def _select_topics_for_cycle(
    all_topics: dict[str, list[str]],
    max_topics: int = 5,
) -> list[tuple[str, str]]:
    """Round-robin topic selection so all stages get covered over ~3 days."""
    rotation_state = await get_config("scout_topic_rotation", {}) or {}
    last_index = int(rotation_state.get("last_index", 0))

    # Flatten all topics with their stage
    flat: list[tuple[str, str]] = []
    for stage, queries in all_topics.items():
        for query in queries:
            flat.append((stage, query))

    if not flat:
        return []

    # Pick next max_topics starting from last_index
    selected = []
    for i in range(max_topics):
        idx = (last_index + i) % len(flat)
        selected.append(flat[idx])

    # Save rotation state
    next_index = (last_index + max_topics) % len(flat)
    await set_config("scout_topic_rotation", {"last_index": next_index, "last_run": time.time()})

    return selected


# ── Tier 1: Search ────────────────────────────────────────────────────


async def _search_sources(topics: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Run Firecrawl searches for each topic across multiple sources."""
    try:
        from tools.firecrawl_client import search_web
    except ImportError:
        logger.warning("Firecrawl client not available for scout")
        return []

    results: list[dict[str, Any]] = []

    for stage, query in topics:
        # Search with 2 source-specific queries per topic for breadth
        source_prefixes = TIER1_SOURCES[:3]  # Top 3 sources per query to stay within rate limits
        for prefix in source_prefixes:
            full_query = f"{prefix} {query}"
            try:
                response = search_web(full_query, limit=3)
                if response.get("status") and response.get("results"):
                    for r in response["results"]:
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "description": r.get("description", ""),
                            "source": prefix.replace("site:", ""),
                            "stage": stage,
                            "query": query,
                        })
            except Exception as e:
                logger.debug("Scout search failed for %s: %s", full_query, e)

    return results


# ── Tier 2: Direct Scrape ─────────────────────────────────────────────


async def _scrape_trending_sources() -> list[dict[str, Any]]:
    """Scrape Tier 2 trending pages. Morning cycle only."""
    try:
        from tools.firecrawl_client import scrape_url
    except ImportError:
        logger.warning("Firecrawl client not available for Tier 2 scrape")
        return []

    results: list[dict[str, Any]] = []

    for url in TIER2_URLS:
        try:
            response = scrape_url(url, formats=["markdown"], only_main_content=True)
            if response.get("status") and response.get("content"):
                content = response["content"]
                title = content.get("title", url)
                excerpt = content.get("markdown_excerpt", "")
                results.append({
                    "title": title,
                    "url": url,
                    "description": excerpt[:500],
                    "source": "tier2_trending",
                    "stage": "general_tools",
                    "query": "trending",
                })
        except Exception as e:
            logger.debug("Scout Tier 2 scrape failed for %s: %s", url, e)

    return results


# ── Deduplication ─────────────────────────────────────────────────────


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


async def _dedup_urls(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter out already-seen URLs. Caps at 2000 entries with 60-day TTL."""
    seen: dict[str, float] = await get_config("scout_seen_urls", {}) or {}
    now = time.time()
    ttl_cutoff = now - (SEEN_URL_TTL_DAYS * 86400)

    # Evict expired entries
    seen = {h: ts for h, ts in seen.items() if ts > ttl_cutoff}

    new_results = []
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        h = _url_hash(url)
        if h not in seen:
            new_results.append(r)
            seen[h] = now

    # Cap at MAX_SEEN_URLS — evict oldest if over
    if len(seen) > MAX_SEEN_URLS:
        sorted_entries = sorted(seen.items(), key=lambda x: x[1])
        seen = dict(sorted_entries[-MAX_SEEN_URLS:])

    await set_config("scout_seen_urls", seen)
    return new_results


# ── LLM Evaluation ───────────────────────────────────────────────────


async def _evaluate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use LLM to evaluate relevance and actionability of raw findings."""
    if not findings:
        return []

    # Batch in groups of 15 to keep prompt size manageable
    evaluated = []
    batch_size = 15

    for i in range(0, len(findings), batch_size):
        batch = findings[i:i + batch_size]
        batch_json = json.dumps(
            [{"title": f.get("title", ""), "url": f.get("url", ""), "description": f.get("description", "")}
             for f in batch],
            indent=2,
        )

        prompt = f"""You are a tech scout for an AI business automation system that:
- Discovers leads (businesses without websites)
- Sends personalized cold emails via Instantly.ai
- Builds websites with Claude + GSAP/Three.js + deploys to Netlify/v0.dev
- Runs local models via Ollama on Mac Studio
- Manages payments via Stripe/Wise

Evaluate these findings for actionability:

{batch_json}

For each finding, return a JSON object in an array:
- relevant: true/false
- pipeline_stage: "lead_discovery"|"email_compose"|"deliverability"|"site_building"|"payments"|"local_models"|"general_tools"
- action_type: "tool_to_try"|"technique_to_adopt"|"competitor_to_watch"|"model_to_test"|"irrelevant"
- summary: 1-2 sentence actionable takeaway
- estimated_value: "low"|"medium"|"high"
- estimated_cost: "free"|"cheap"|"moderate"|"expensive"

Examples:
- {{"title": "New AI site builder with API access"}} → {{"relevant": true, "pipeline_stage": "site_building", "action_type": "tool_to_try", "summary": "Has REST API for programmatic site generation, could compete with v0.dev", "estimated_value": "high", "estimated_cost": "moderate"}}
- {{"title": "Best hiking trails in Colorado"}} → {{"relevant": false, "action_type": "irrelevant", "summary": "", "estimated_value": "low", "estimated_cost": "free"}}
- {{"title": "Cold email trick: ask one question only"}} → {{"relevant": true, "pipeline_stage": "email_compose", "action_type": "technique_to_adopt", "summary": "Single-question subject lines showed 2.3x reply rates in B2B tests", "estimated_value": "medium", "estimated_cost": "free"}}

Return ONLY a JSON array. No explanation."""

        try:
            result = await llm.generate(
                prompt,
                model="local-heavy",
                max_tokens=2000,
                temperature=0.1,
                pipeline_stage="scout:evaluate_findings",
            )
            start = result.find("[")
            end = result.rfind("]") + 1
            if start < 0 or end <= 0:
                logger.debug("Scout evaluation: no JSON array found in LLM response")
            elif start >= 0 and end > start:
                parsed = json.loads(result[start:end])
                for j, item in enumerate(parsed):
                    if j < len(batch):
                        item["url"] = batch[j].get("url", "")
                        item["title"] = batch[j].get("title", "")
                        item["source"] = batch[j].get("source", "")
                        item["original_stage"] = batch[j].get("stage", "")
                        item["source_origin"] = "scout"
                        evaluated.append(item)
        except Exception as e:
            logger.debug("Scout evaluation batch failed: %s", e)

    return evaluated


# ── Store and Act ─────────────────────────────────────────────────────


async def _store_and_act(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Store relevant findings and dispatch actions for high-value ones."""
    from shared.comms import delegate_task, record_decision, store_learning

    stored = 0
    tools_dispatched = 0
    techniques_stored = 0
    models_flagged = 0

    for f in findings:
        if not f.get("relevant"):
            continue

        url = f.get("url", "")
        summary = f.get("summary", "")
        action_type = f.get("action_type", "")
        stage = f.get("pipeline_stage", "general_tools")
        value = f.get("estimated_value", "low")
        cost = f.get("estimated_cost", "free")

        if not summary:
            continue

        # Store learning
        try:
            await store_learning(
                category=stage,
                insight=f"[SCOUT] {summary}",
                confidence=0.3,
                source_agent="scout",
            )
        except Exception as e:
            logger.debug("Failed to store scout learning: %s", e)

        # Store vector memory
        try:
            from titan.memory import store_memory
            await store_memory(
                content=f"[SCOUT] {summary} (source: {url})",
                category=f"scout_{stage}",
            )
        except Exception as e:
            logger.debug("Failed to store scout memory: %s", e)

        # Record decision for audit trail
        try:
            await record_decision(
                agent="scout",
                decision_type="scout_finding",
                context={"url": url, "source": f.get("source", ""), "origin": "external_intelligence"},
                decision={"action_type": action_type, "summary": summary, "estimated_value": value, "estimated_cost": cost},
                reasoning=f"Found via scout search: {f.get('title', '')}",
            )
        except Exception as e:
            logger.debug("Failed to record decision: %s", e)

        # Emit event for Hermes/dashboard
        await emit_event("scout_finding", {
            "url": url,
            "title": f.get("title", ""),
            "summary": summary,
            "action_type": action_type,
            "pipeline_stage": stage,
            "value": value,
            "cost": cost,
            "source_origin": "scout",
        })
        stored += 1

        # Act on high-value tool discoveries
        if action_type == "tool_to_try" and value == "high":
            try:
                await delegate_task(
                    "scout",
                    "clawdbot",
                    "capability_resolve",
                    {"need": summary[:100], "context": {"source_url": url, "summary": summary}},
                    priority=5,
                )
                tools_dispatched += 1
            except Exception as e:
                logger.debug("Failed to dispatch tool finding: %s", e)

            # Also dispatch to Ruflo for implementation evaluation
            ruflo_config = getattr(config, "ruflo", None)
            if getattr(ruflo_config, "enabled", False):
                try:
                    await delegate_task(
                        "scout", "ruflo", "implement_tool",
                        {"discovery": {"summary": summary[:300], "url": url, "action_type": action_type},
                         "source": "scout", "evaluation_only": True},
                        priority=7,
                    )
                except Exception as e:
                    logger.debug("Failed to dispatch tool to Ruflo: %s", e)

        # Act on technique findings — create a scout rule
        elif action_type == "technique_to_adopt" and value in ("medium", "high"):
            try:
                await _create_scout_rule(f)
                techniques_stored += 1
            except Exception as e:
                logger.debug("Failed to create scout rule: %s", e)

        # Flag new models
        elif action_type == "model_to_test":
            try:
                await emit_event("scout_model_found", {
                    "url": url,
                    "summary": summary,
                    "pipeline_stage": stage,
                })
                models_flagged += 1
            except Exception as e:
                logger.debug("Failed to emit model finding event: %s", e)

    return {
        "stored": stored,
        "tools_dispatched": tools_dispatched,
        "techniques_stored": techniques_stored,
        "models_flagged": models_flagged,
        "actionable": tools_dispatched + techniques_stored + models_flagged,
    }


async def _create_scout_rule(finding: dict[str, Any]) -> None:
    """Extract a concrete testable rule from a technique finding and insert into titan_rules."""
    url = finding.get("url", "")
    summary = finding.get("summary", "")
    stage = finding.get("pipeline_stage", "general_tools")

    # Scrape the source for more detail if URL available
    detail = summary
    if url:
        try:
            from tools.firecrawl_client import scrape_url
            scraped = scrape_url(url, formats=["markdown"], only_main_content=True)
            if scraped.get("status") and scraped.get("content"):
                excerpt = scraped["content"].get("markdown_excerpt", "")
                if excerpt:
                    detail = f"{summary}\n\nSource detail: {excerpt[:800]}"
        except Exception as e:
            logger.debug("Failed to store scout rule: %s", e)

    # Ask LLM to extract a concrete, testable rule
    prompt = f"""Extract ONE specific, testable rule from this finding:

Finding: {detail}
Pipeline stage: {stage}

The rule must be:
- A concrete behavioral change (not vague advice)
- Testable with metrics (we can measure if it helped)
- Applicable to our pipeline ({stage})

Return JSON:
{{"rule_text": "specific rule", "metric_name": "what to measure", "category": "{stage}"}}

Return ONLY JSON, no explanation."""

    try:
        result = await llm.generate(prompt, model="fast", max_tokens=300, temperature=0.1)
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(result[start:end])
            rule_text = parsed.get("rule_text", "")
            metric_name = parsed.get("metric_name", "")
            category = parsed.get("category", stage)

            if rule_text and len(rule_text) > 10:
                from shared.db import execute
                await execute(
                    """INSERT INTO titan_rules (category, rule_text, metric_name, confidence, source, active)
                       VALUES (%s, %s, %s, 0.3, 'scout', TRUE)
                       ON CONFLICT (category, rule_text) DO NOTHING""",
                    (category, rule_text, metric_name),
                )
                logger.info("Scout rule created: [%s] %s", category, rule_text[:80])
    except Exception as e:
        logger.debug("Failed to extract scout rule: %s", e)
