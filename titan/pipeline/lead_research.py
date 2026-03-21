"""
Stage 2: Lead Research
Deep research each prospect — scrape their info, summarize with LLM.
"""

import json
import logging

from shared.db import fetch_all, execute
from shared.llm_client import llm
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.research")


async def research_leads(batch_size: int = 10):
    """Research all discovered leads that haven't been researched yet."""
    from shared.db import fetch_all

    leads = await fetch_all(
        """SELECT id, business_name, email, industry, website_url, country, city
           FROM clients WHERE status = 'discovered'
           ORDER BY created_at ASC LIMIT %s""",
        (batch_size,),
    )

    for lead in leads:
        try:
            await _research_one(lead)
        except Exception as e:
            logger.error(f"Research failed for lead {lead['id']}: {e}")


async def _research_one(lead: dict):
    """Deep research a single lead."""
    lead_id = lead["id"]
    business_name = lead["business_name"]

    # Try to scrape their existing web presence
    web_info = await _scrape_business_info(lead)

    # Use LLM to create a research summary
    prompt = f"""You are researching a business to sell them a website.

Business: {business_name}
Email: {lead.get('email', 'unknown')}
Industry: {lead.get('industry', 'unknown')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Current website: {lead.get('website_url', 'none')}
Additional info found: {web_info}

Write a brief research summary (3-5 sentences) that includes:
1. What this business does
2. Why they need a (better) website
3. What specific benefits a website would bring them
4. Any personalization hooks for the outreach email

Also score this lead 0-100 on likelihood to buy.

Return JSON:
{{
    "summary": "...",
    "lead_score": 75,
    "personalization_hooks": ["hook1", "hook2"],
    "estimated_industry": "...",
    "language": "en"
}}"""

    result = await llm.generate(prompt, model="fast", temperature=0.5)

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        data = {
            "summary": f"Business: {business_name}. Needs further research.",
            "lead_score": 50,
            "personalization_hooks": [],
            "estimated_industry": lead.get("industry", "unknown"),
            "language": "en",
        }

    # Update the lead with research data
    await execute(
        """UPDATE clients SET
               research_summary = %s,
               lead_score = %s,
               industry = COALESCE(NULLIF(%s, ''), industry),
               language = %s,
               updated_at = NOW()
           WHERE id = %s""",
        (
            data.get("summary", ""),
            data.get("lead_score", 50),
            data.get("estimated_industry", ""),
            data.get("language", "en"),
            lead_id,
        ),
    )

    await transition_lead(lead_id, "researched")
    logger.info(f"Researched lead {lead_id}: {business_name} (score: {data.get('lead_score', 0)})")


async def _scrape_business_info(lead: dict) -> str:
    """Try to scrape additional info about the business using Firecrawl."""
    try:
        from tools.firecrawl_client import scrape_url, enrich_business_profile

        if lead.get("website_url"):
            result = scrape_url(lead["website_url"])
            if result.get("mode") == "live":
                content = result.get("content", {})
                return content.get("markdown", content.get("description", ""))[:1000]

        # Enrich via search + scrape combo
        profile = enrich_business_profile(
            business_name=lead.get("business_name", ""),
            city=lead.get("city", ""),
            industry=lead.get("industry", ""),
            website_url=lead.get("website_url", ""),
        )
        if profile.get("mode") == "live":
            parts = []
            for sr in profile.get("search_results", [])[:2]:
                parts.append(f"{sr.get('title', '')}: {sr.get('description', '')}")
            extract = profile.get("website_extract", {})
            if extract:
                parts.append(extract.get("markdown_excerpt", ""))
            return "\n".join(parts)[:1000]

        return "No additional info found."
    except Exception:
        return "No additional info found."
