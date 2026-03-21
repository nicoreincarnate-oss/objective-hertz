"""
Stage 2: Lead Research
Deep research each prospect — scrape their info, summarize with LLM.
"""

import asyncio
import json
import logging

from shared.db import fetch_all, execute
from shared.comms import request_task_result
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
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

    semaphore = asyncio.Semaphore(4)
    await asyncio.gather(
        *[_research_with_limit(lead, semaphore) for lead in leads],
        return_exceptions=False,
    )


async def _research_with_limit(lead: dict, semaphore: asyncio.Semaphore):
    """Research one lead while keeping total concurrent local work bounded."""
    async with semaphore:
        try:
            await _research_one(lead)
        except Exception as e:
            logger.error(f"Research failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("lead_research", e, lead_id=lead["id"])


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

    data = await _generate_research_data(prompt, lead)

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
    """Try to scrape additional info about the business via ClawdBot first."""
    try:
        from tools.firecrawl_client import scrape_url, enrich_business_profile

        scrape_task = None
        browser_task = None
        if lead.get("website_url"):
            scrape_task = request_task_result(
                "web_scrape",
                payload={"url": lead["website_url"]},
                timeout_seconds=45,
            )
            browser_task = request_task_result(
                "browser_task",
                payload={
                    "url": lead["website_url"],
                    "description": f"Inspect {lead.get('business_name', 'the business website')} and summarize what the business does.",
                },
                timeout_seconds=60,
            )
        enrich_task = request_task_result(
            "enrich_lead",
            payload={"client_id": lead["id"]},
            timeout_seconds=60,
        )

        scrape_result, browser_result, enrich_result = await asyncio.gather(
            scrape_task if scrape_task else asyncio.sleep(0, result=None),
            browser_task if browser_task else asyncio.sleep(0, result=None),
            enrich_task,
            return_exceptions=True,
        )

        parts = []

        scraped_content = {}
        if not isinstance(scrape_result, Exception) and scrape_result and scrape_result.get("ok"):
            scraped_content = scrape_result.get("result", {}).get("result", {})
        elif lead.get("website_url"):
            scraped_content = scrape_url(lead["website_url"])
        if scraped_content.get("mode") == "live":
            content = scraped_content.get("content", {})
            parts.append(content.get("markdown", content.get("description", ""))[:700])

        if not isinstance(browser_result, Exception) and browser_result and browser_result.get("ok"):
            browser_summary = browser_result.get("result", {}).get("result", "")
            if browser_summary:
                parts.append(str(browser_summary)[:400])

        if not isinstance(enrich_result, Exception) and enrich_result and enrich_result.get("ok"):
            profile = enrich_result.get("result", {}).get("profile", {})
        else:
            profile = enrich_business_profile(
                business_name=lead.get("business_name", ""),
                city=lead.get("city", ""),
                industry=lead.get("industry", ""),
                website_url=lead.get("website_url", ""),
            )
        if profile.get("mode") == "live":
            for sr in profile.get("search_results", [])[:2]:
                parts.append(f"{sr.get('title', '')}: {sr.get('description', '')}")
            extract = profile.get("website_extract", {})
            if extract:
                parts.append(extract.get("markdown_excerpt", ""))

        if parts:
            return "\n".join(part for part in parts if part).strip()[:1000]

        return "No additional info found."
    except Exception:
        return "No additional info found."


async def _generate_research_data(prompt: str, lead: dict) -> dict:
    """Run a local-first research pass, escalating to smart only for weak/high-value cases."""
    data = await _run_research_prompt(prompt, model="fast", lead=lead)
    if _should_upgrade_research_pass(data):
        upgraded = await _run_research_prompt(prompt, model="smart", lead=lead)
        if upgraded.get("summary"):
            return upgraded
    return data


async def _run_research_prompt(prompt: str, *, model: str, lead: dict) -> dict:
    result = await llm.generate(prompt, model=model, temperature=0.5)

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        return json.loads(result[start:end])
    except (json.JSONDecodeError, ValueError):
        return {
            "summary": f"Business: {lead['business_name']}. Needs further research.",
            "lead_score": 50,
            "personalization_hooks": [],
            "estimated_industry": lead.get("industry", "unknown"),
            "language": "en",
        }


def _should_upgrade_research_pass(data: dict) -> bool:
    """Reserve Claude for leads where the local pass looks weak or especially promising."""
    summary = str(data.get("summary", "") or "").strip()
    hooks = data.get("personalization_hooks") or []
    lead_score = int(data.get("lead_score", 0) or 0)
    return len(summary) < 120 or not hooks or lead_score >= 75
