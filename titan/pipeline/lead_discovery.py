"""
Stage 1: Lead Discovery
Find businesses without websites globally.
AI picks the best sources and learns what works.
"""

import json
import logging
import random

try:
    from tools.apify_client import apify_available, discover_leads as apify_discover, enrich_lead_emails
    HAS_APIFY = True
except ImportError:
    HAS_APIFY = False

from shared.comms import request_task_result
from shared.db import emit_event, fetch_one, get_config
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from shared.skill_loader import execute_skill, find_skill
from titan.memory import get_relevant_learnings

logger = logging.getLogger("perseus.titan.discovery")

# Skills to try for lead discovery (in priority order)
DISCOVERY_SKILLS = [
    "apify-lead-generation",     # Scrapes Google Maps, LinkedIn, Instagram
    "outbound-prospecting",      # Structured lead research workflow
    "smart-web-scraper",         # Extract structured data from web pages
    "openclaw-free-web-search",  # Free self-hosted web search
    "firecrawl-search",          # Firecrawl-based search
]

DISCOVERY_SOURCE_DESCRIPTIONS = {
    "apify-lead-generation": "Best for broad local-business discovery across maps and social sources.",
    "outbound-prospecting": "Structured outbound prospecting workflow with higher-quality lead selection.",
    "smart-web-scraper": "Useful when discovery needs structured extraction from web results.",
    "openclaw-free-web-search": "Free self-hosted search option when paid providers are weak or unavailable.",
    "firecrawl-search": "Firecrawl-based search skill when installed and working.",
    "custom_firecrawl": "Built-in Firecrawl web search fallback implemented in this codebase.",
}


async def discover_leads(batch_size: int = 20) -> list[int]:
    """
    Discover new leads — businesses that need websites.
    AI chooses the best available discovery source order.
    Returns list of new client IDs.
    """
    strategy = await _get_discovery_strategy()

    # Prefer Apify as a direct source when available — returns structured,
    # high-quality Google Maps data without needing skill orchestration.
    if HAS_APIFY and await apify_available():
        apify_leads = await _discover_with_apify(strategy, batch_size)
        if apify_leads:
            logger.info(f"Apify produced {len(apify_leads)} leads, skipping skill-based discovery")
            return apify_leads
        logger.info("Apify returned no leads, falling through to skill-based discovery")

    source_plan = await _choose_discovery_sources(strategy, batch_size)
    source_plan = await _apply_discovery_overrides(source_plan)
    new_lead_ids: list[int] = []

    for source_name in source_plan["source_order"]:
        remaining = batch_size - len(new_lead_ids)
        if remaining <= 0:
            break

        shadow_opportunity_id = (
            source_plan.get("shadow_opportunity_id")
            if source_name == source_plan.get("shadow_skill")
            else None
        )

        if source_name == "custom_firecrawl":
            discovered = await _discover_custom(remaining, strategy)
        else:
            logger.info(f"Using AI-selected discovery skill '{source_name}'")
            discovered = await _discover_with_skill(
                source_name,
                remaining,
                strategy,
                shadow_opportunity_id=shadow_opportunity_id,
            )

        for lead_id in discovered:
            if lead_id not in new_lead_ids:
                new_lead_ids.append(lead_id)

        if len(new_lead_ids) >= batch_size:
            break

    return new_lead_ids


async def _discover_with_apify(strategy: dict, batch_size: int) -> list[int]:
    """Direct Apify discovery — bypasses skill orchestration for structured Google Maps data."""
    queries = strategy.get("search_queries", [])
    regions = strategy.get("target_regions", [""])
    location = regions[0] if regions else ""

    new_lead_ids: list[int] = []

    for query in queries:
        remaining = batch_size - len(new_lead_ids)
        if remaining <= 0:
            break

        try:
            raw_leads = await apify_discover(
                query=query,
                location=location,
                max_results=remaining,
            )

            # Optionally enrich with emails (best-effort)
            try:
                raw_leads = await enrich_lead_emails(raw_leads)
            except Exception as e:
                logger.debug(f"Apify email enrichment skipped: {e}")

            for lead in raw_leads:
                lead_id = await _store_lead(_convert_apify_lead(lead))
                if lead_id and lead_id not in new_lead_ids:
                    new_lead_ids.append(lead_id)
        except Exception as e:
            logger.error(f"Apify discovery failed for query '{query}': {e}")
            await emit_pipeline_error(
                "lead_discovery.apify", e, context={"query": query}
            )

    if new_lead_ids:
        await emit_event("leads_discovered", {"count": len(new_lead_ids), "source": "apify"})
    return new_lead_ids


def _convert_apify_lead(apify_lead: dict) -> dict:
    """Convert Apify lead format to the pipeline's expected format."""
    return {
        "business_name": apify_lead.get("name", ""),
        "contact_name": "",
        "email": apify_lead.get("email", ""),
        "phone": apify_lead.get("phone", ""),
        "industry": apify_lead.get("category", ""),
        "website_url": apify_lead.get("website", ""),
        "country": "",
        "city": apify_lead.get("city", "") or apify_lead.get("state", ""),
        "source": "apify",
    }


async def _discover_with_skill(
    skill_name: str,
    batch_size: int,
    strategy: dict,
    *,
    shadow_opportunity_id: int | None = None,
) -> list[int]:
    """Use an installed skill for lead discovery."""
    task_prompt = f"""Find {batch_size} businesses that don't have a website.
Search queries: {json.dumps(strategy['search_queries'])}
Target industries: {json.dumps(strategy.get('target_industries', []))}

For each business found, return JSON array:
[{{
  "business_name": "...", "email": "...", "phone": "...", "industry": "...",
  "city": "...", "country": "...", "website_url": "", "source": "{skill_name}"
}}]"""

    task_result = await request_task_result(
        "skill_execute",
        payload={
            "skill_name": skill_name,
            "prompt": task_prompt,
            "context": {"batch_size": str(batch_size)},
        },
        timeout_seconds=120,
    )

    if task_result and task_result.get("ok"):
        result = task_result.get("result", {}).get("result", "")
    else:
        logger.warning("ClawdBot skill execution unavailable, falling back to local skill execution")
        result = await execute_skill(
            skill_name,
            task_prompt=task_prompt,
            context={"batch_size": str(batch_size)},
        )

    # Parse results and store leads
    new_lead_ids = []
    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        businesses = json.loads(result[start:end]) if start >= 0 else []
        for biz in businesses[:batch_size]:
            if shadow_opportunity_id:
                biz["source_campaign"] = f"expansion:{shadow_opportunity_id}"
            lead_id = await _store_lead(biz)
            if lead_id:
                new_lead_ids.append(lead_id)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Could not parse skill output for {skill_name}")
        await emit_pipeline_error(
            "lead_discovery.skill_parse",
            ValueError(f"Could not parse skill output for {skill_name}"),
            context={"skill": skill_name},
        )

    if new_lead_ids:
        await emit_event("leads_discovered", {"count": len(new_lead_ids), "skill": skill_name})
    else:
        await _emit_discovery_empty(
            source="skill",
            strategy=strategy,
            details={"skill": skill_name},
        )
    return new_lead_ids


async def _apply_discovery_overrides(source_plan: dict) -> dict:
    """Apply revenue expansion overrides after AI chooses the default source order."""
    preferred_skill = str(await get_config("preferred_discovery_skill", "") or "").strip()
    shadow_skill = str(await get_config("active_shadow_discovery_skill", "") or "").strip()
    shadow_percent = int(await get_config("expansion_shadow_percent", 10) or 10)
    shadow_opportunity_id = int(await get_config("active_shadow_opportunity_id", 0) or 0)

    source_order = list(source_plan.get("source_order", []))

    if preferred_skill and find_skill(preferred_skill):
        source_order = [skill for skill in source_order if skill != preferred_skill]
        source_order.insert(0, preferred_skill)

    plan = {
        **source_plan,
        "source_order": source_order[:3],
    }

    if shadow_skill and shadow_opportunity_id and shadow_percent > 0 and find_skill(shadow_skill):
        roll = random.randint(1, 100)
        if roll <= shadow_percent:
            source_order = [skill for skill in plan["source_order"] if skill != shadow_skill]
            source_order.insert(0, shadow_skill)
            plan["source_order"] = source_order[:3]
            plan["shadow_skill"] = shadow_skill
            plan["shadow_opportunity_id"] = shadow_opportunity_id
            await emit_event(
                "revenue_expansion_shadow_applied",
                {
                    "opportunity_id": shadow_opportunity_id,
                    "skill": shadow_skill,
                    "roll": roll,
                    "shadow_percent": shadow_percent,
                },
            )

    return plan


async def _discover_custom(batch_size: int, strategy: dict) -> list[int]:
    """Fallback: custom discovery using Firecrawl + LLM."""
    new_lead_ids = []
    successful_queries = 0

    for query in strategy["search_queries"]:
        try:
            results = await _search_for_businesses(query)
            if results:
                successful_queries += 1
            for biz in results[:batch_size]:
                lead_id = await _store_lead(biz)
                if lead_id:
                    new_lead_ids.append(lead_id)
        except Exception as e:
            logger.error(f"Discovery search failed for '{query}': {e}")
            await emit_pipeline_error("lead_discovery.search", e, context={"query": query})

    if new_lead_ids:
        await emit_event("leads_discovered", {
            "count": len(new_lead_ids),
            "strategy": strategy.get("reasoning", ""),
        })
        logger.info(f"Discovered {len(new_lead_ids)} new leads")
    else:
        await _emit_discovery_empty(
            source="custom",
            strategy=strategy,
            details={
                "query_count": len(strategy.get("search_queries", [])),
                "successful_queries": successful_queries,
            },
        )

    return new_lead_ids


async def _choose_discovery_sources(strategy: dict, batch_size: int) -> dict:
    """Ask AI which discovery sources to use, based on what is actually available."""
    available_sources = []
    for skill_name in DISCOVERY_SKILLS:
        if find_skill(skill_name):
            available_sources.append({
                "name": skill_name,
                "type": "skill",
                "description": DISCOVERY_SOURCE_DESCRIPTIONS.get(skill_name, ""),
            })

    available_sources.append({
        "name": "custom_firecrawl",
        "type": "fallback",
        "description": DISCOVERY_SOURCE_DESCRIPTIONS["custom_firecrawl"],
    })

    if len(available_sources) == 1:
        return {
            "source_order": ["custom_firecrawl"],
            "reasoning": "No discovery skills installed; using built-in Firecrawl fallback.",
        }

    prompt = f"""You are Titan choosing the best discovery sources for this run.

Goal: find {batch_size} businesses that need websites.
Strategy:
{json.dumps(strategy)}

Available sources:
{json.dumps(available_sources)}

Choose an ordered list of source names to try for this run.
Rules:
1. Pick from the available source names only.
2. Put the best source first.
3. Keep custom_firecrawl as a fallback unless it is clearly the best choice.
4. Return at most 3 sources.

Return JSON:
{{
  "source_order": ["source1", "source2"],
  "reasoning": "why this order"
}}"""

    result = await llm.generate(prompt, model="fast", temperature=0.3, operation="titan._choose_discovery_sources", daemon_name="titan")

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
        valid_names = {source["name"] for source in available_sources}
        source_order = [
            source_name
            for source_name in data.get("source_order", [])
            if source_name in valid_names
        ]
        if not source_order:
            raise ValueError("No valid discovery sources selected")
        if "custom_firecrawl" not in source_order:
            source_order.append("custom_firecrawl")
        return {
            "source_order": source_order[:3],
            "reasoning": data.get("reasoning", ""),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        default_order = [source["name"] for source in available_sources]
        return {
            "source_order": default_order[:3],
            "reasoning": "Fallback source order based on installed skills and built-in fallback.",
        }


async def _emit_discovery_empty(source: str, strategy: dict, details: dict | None = None) -> None:
    """Surface zero-result discovery runs so broken sources don't fail silently."""
    payload = {
        "source": source,
        "reasoning": strategy.get("reasoning", ""),
        "search_queries": strategy.get("search_queries", [])[:5],
        **(details or {}),
    }
    await emit_event("lead_discovery_empty", payload)
    await emit_pipeline_error(
        "lead_discovery.empty",
        RuntimeError("Lead discovery returned zero leads"),
        context=payload,
    )


async def _get_discovery_strategy() -> dict:
    """Ask AI what to search for based on learnings and proven rules."""
    # Get relevant learnings from structured DB + vector memory
    insights = await get_relevant_learnings(
        "lead discovery, target industries, regions that convert, businesses without websites",
        query_type="lead_research",
    )

    # Get proven targeting rules
    from titan.memory import format_rules_for_prompt
    rules_block = await format_rules_for_prompt(["targeting", "industry"])

    prompt = f"""You are Titan, an AI that finds businesses without websites.

Based on these learnings from past discovery:
{insights}

{rules_block}

Generate 5 search queries to find businesses that:
1. Don't have a website (or have a terrible one)
2. Are in industries where a website drives revenue
3. Are likely to pay $200-325 for a professional website
4. Are reachable by email

Return JSON:
{{
    "search_queries": ["query1", "query2", ...],
    "target_industries": ["industry1", ...],
    "target_regions": ["region1", ...],
    "reasoning": "why these targets"
}}"""

    result = await llm.generate(prompt, model="fast", temperature=0.8, operation="titan._get_discovery_strategy", daemon_name="titan")
    try:
        # Extract JSON from response
        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        # Fallback strategy
        return {
            "search_queries": [
                "small business no website plumber",
                "local restaurant no website near me",
                "dentist office needs website",
                "hair salon no online presence",
                "auto repair shop no website",
            ],
            "target_industries": ["plumber", "restaurant", "dentist", "salon", "auto repair"],
            "target_regions": ["global"],
            "reasoning": "Default high-value local service industries",
        }


async def _search_for_businesses(query: str) -> list[dict]:
    """
    Search for businesses using available tools.
    Wraps tools/firecrawl_client.py for web extraction.
    """
    # Use Firecrawl for web search
    try:
        from tools.firecrawl_client import search_web
    except ImportError:
        logger.warning("Firecrawl client not available")
        return []

    try:
        result = search_web(query, limit=10)
        if result.get("mode") != "live":
            logger.warning(f"Firecrawl not available: {result.get('summary', '')}")
            return []
        # Extract business info from search results
        raw_results = result.get("results", [])
        businesses = []
        for item in raw_results:
            if isinstance(item, dict):
                businesses.append({
                    "business_name": item.get("title", ""),
                    "email": "",  # Needs enrichment in research stage
                    "website_url": item.get("url", ""),
                    "source": "firecrawl",
                })
        return businesses
    except Exception as e:
        logger.error(f"Firecrawl search failed: {e}")
        return []


async def _store_lead(business: dict) -> int | None:
    """Store a discovered lead in the database. Returns client_id or None if duplicate.

    Leads without email are stored — they get enriched in the research stage.
    Dedup: by email if present, otherwise by business_name + source.
    """
    email = business.get("email", "").strip()
    name = business.get("business_name", business.get("name", "")).strip()
    source = business.get("source", "firecrawl")
    source_campaign = business.get("source_campaign", "")

    if not name and not email:
        return None  # Need at least a name or email

    # Insert with conflict handling — the DB unique index is the real
    # dedup enforcement. ON CONFLICT DO NOTHING handles races where two
    # workers discover the same lead concurrently.
    try:
        row = await fetch_one(
            """INSERT INTO clients (business_name, contact_name, email, phone, industry,
                                   website_url, status, country, city, source, source_campaign)
               VALUES (%s, %s, %s, %s, %s, %s, 'discovered', %s, %s, %s, %s)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            (
                name,
                business.get("contact_name", ""),
                email,
                business.get("phone", ""),
                business.get("industry", ""),
                business.get("website_url", ""),
                business.get("country", ""),
                business.get("city", ""),
                source,
                source_campaign,
            ),
        )
        return row["id"] if row else None
    except Exception as e:
        logger.debug(f"Lead insert failed (likely duplicate): {e}")
        return None
