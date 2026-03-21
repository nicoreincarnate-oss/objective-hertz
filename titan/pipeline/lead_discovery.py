"""
Stage 1: Lead Discovery
Find businesses without websites globally.
AI picks the best sources and learns what works.
"""

import json
import logging
from typing import Optional

from shared.db import fetch_all, fetch_one, execute, emit_event
from shared.llm_client import llm
from shared.skill_loader import find_skill, execute_skill
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


async def discover_leads(batch_size: int = 20) -> list[int]:
    """
    Discover new leads — businesses that need websites.
    Tries installed skills first, falls back to custom Firecrawl code.
    Returns list of new client IDs.
    """
    # Check if any discovery skills are installed
    for skill_name in DISCOVERY_SKILLS:
        skill_path = find_skill(skill_name)
        if skill_path:
            logger.info(f"Using skill '{skill_name}' for lead discovery")
            return await _discover_with_skill(skill_name, batch_size)

    # Fallback: custom discovery logic
    logger.info("No discovery skills found, using custom Firecrawl logic")
    return await _discover_custom(batch_size)


async def _discover_with_skill(skill_name: str, batch_size: int) -> list[int]:
    """Use an installed skill for lead discovery."""
    strategy = await _get_discovery_strategy()

    result = await execute_skill(
        skill_name,
        task_prompt=f"""Find {batch_size} businesses that don't have a website.
Search queries: {json.dumps(strategy['search_queries'])}
Target industries: {json.dumps(strategy.get('target_industries', []))}

For each business found, return JSON array:
[{{"business_name": "...", "email": "...", "phone": "...", "industry": "...",
   "city": "...", "country": "...", "website_url": "", "source": "{skill_name}"}}]""",
        context={"batch_size": str(batch_size)},
    )

    # Parse results and store leads
    new_lead_ids = []
    try:
        start = result.find("[")
        end = result.rfind("]") + 1
        businesses = json.loads(result[start:end]) if start >= 0 else []
        for biz in businesses[:batch_size]:
            lead_id = await _store_lead(biz)
            if lead_id:
                new_lead_ids.append(lead_id)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Could not parse skill output for {skill_name}")

    if new_lead_ids:
        await emit_event("leads_discovered", {"count": len(new_lead_ids), "skill": skill_name})
    return new_lead_ids


async def _discover_custom(batch_size: int) -> list[int]:
    """Fallback: custom discovery using Firecrawl + LLM."""
    strategy = await _get_discovery_strategy()
    new_lead_ids = []

    for query in strategy["search_queries"]:
        try:
            results = await _search_for_businesses(query)
            for biz in results[:batch_size]:
                lead_id = await _store_lead(biz)
                if lead_id:
                    new_lead_ids.append(lead_id)
        except Exception as e:
            logger.error(f"Discovery search failed for '{query}': {e}")

    if new_lead_ids:
        await emit_event("leads_discovered", {
            "count": len(new_lead_ids),
            "strategy": strategy.get("reasoning", ""),
        })
        logger.info(f"Discovered {len(new_lead_ids)} new leads")

    return new_lead_ids


async def _get_discovery_strategy() -> dict:
    """Ask AI what to search for based on learnings."""
    # Get relevant learnings from structured DB + vector memory
    insights = await get_relevant_learnings(
        "lead discovery, target industries, regions that convert, businesses without websites"
    )

    prompt = f"""You are Titan, an AI that finds businesses without websites.

Based on these learnings from past discovery:
{insights}

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

    result = await llm.generate(prompt, model="fast", temperature=0.8)
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
    except ImportError:
        logger.warning("Firecrawl client not available")
        return []
    except Exception as e:
        logger.error(f"Firecrawl search failed: {e}")
        return []


async def _store_lead(business: dict) -> Optional[int]:
    """Store a discovered lead in the database. Returns client_id or None if duplicate.

    Leads without email are stored — they get enriched in the research stage.
    Dedup: by email if present, otherwise by business_name + source.
    """
    email = business.get("email", "").strip()
    name = business.get("business_name", business.get("name", "")).strip()
    source = business.get("source", "firecrawl")

    if not name and not email:
        return None  # Need at least a name or email

    # Check for duplicate
    if email:
        existing = await fetch_one(
            "SELECT id FROM clients WHERE email = %s", (email,)
        )
    else:
        existing = await fetch_one(
            "SELECT id FROM clients WHERE business_name = %s AND source = %s AND email = ''",
            (name, source),
        )
    if existing:
        return None

    row = await fetch_one(
        """INSERT INTO clients (business_name, contact_name, email, phone, industry,
                               website_url, status, country, city, source)
           VALUES (%s, %s, %s, %s, %s, %s, 'discovered', %s, %s, %s)
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
        ),
    )
    return row["id"] if row else None
