"""
Stage 2: Lead Research
Deep research each prospect — scrape their info, summarize with LLM.
"""

import asyncio
import json
import logging
import re
from typing import Any

from shared.comms import request_task_result
from shared.db import execute, fetch_all
from shared.llm_client import llm
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.research")


async def research_leads(batch_size: int = 10):
    """Research all discovered leads that haven't been researched yet."""

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
    raw_research = await _scrape_business_info(lead)
    research_payload = _normalize_research_payload(raw_research)
    web_info = research_payload["summary"]
    structured_context = _extract_structured_business_context(web_info, lead)
    reference_sites = _select_reference_sites(research_payload.get("search_results", []), lead)
    structured_facts_json = json.dumps(structured_context["facts"], ensure_ascii=False)
    structured_graph_json = json.dumps(structured_context["graph"], ensure_ascii=False)

    # Use LLM to create a research summary
    prompt = f"""You are researching a business to sell them a website.

Business: {business_name}
Email: {lead.get('email', 'unknown')}
Industry: {lead.get('industry', 'unknown')}
Location: {lead.get('city', '')}, {lead.get('country', '')}
Current website: {lead.get('website_url', 'none')}
Additional info found: {web_info}

Structured facts extracted from the scraped text:
{json.dumps(structured_context["facts"], ensure_ascii=False, indent=2)}

Relationship graph extracted from the scraped text:
{json.dumps(structured_context["graph"], ensure_ascii=False, indent=2)}

Candidate inspiration sites from live research:
{json.dumps(reference_sites, ensure_ascii=False, indent=2) if reference_sites else '[]'}

Use the structured facts and graph to surface concrete personalization hooks.

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
    "language": "en",
    "reference_patterns": ["pattern1", "pattern2"],
    "anti_patterns": ["anti1", "anti2"],
    "design_positioning": "premium|minimal|editorial|playful|luxury|trust-first"
}}"""

    data = await _generate_research_data(prompt, lead)
    enriched_facts = dict(structured_context["facts"])
    design_reference_packet = _build_design_reference_packet(
        lead=lead,
        reference_sites=reference_sites,
        structured_context=structured_context,
        research_data=data,
    )
    enriched_facts.update(design_reference_packet)
    structured_facts_json = json.dumps(enriched_facts, ensure_ascii=False)

    # Update the lead with research data
    await execute(
        """UPDATE clients SET
               research_summary = %s,
               lead_score = %s,
               industry = COALESCE(NULLIF(%s, ''), industry),
               language = %s,
               research_facts = %s,
               research_graph = %s,
               updated_at = NOW()
           WHERE id = %s""",
        (
            data.get("summary", ""),
            data.get("lead_score", 50),
            data.get("estimated_industry", ""),
            data.get("language", "en"),
            structured_facts_json if structured_facts_json else "{}",
            structured_graph_json if structured_graph_json else "{}",
            lead_id,
        ),
    )

    await transition_lead(lead_id, "researched")
    logger.info(f"Researched lead {lead_id}: {business_name} (score: {data.get('lead_score', 0)})")


async def _scrape_business_info(lead: dict) -> str | dict:
    """Try to scrape additional info about the business via ClawdBot first."""
    try:
        from tools.firecrawl_client import enrich_business_profile, scrape_url

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

        scrape_result: Any
        browser_result: Any
        enrich_result: Any
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
            return {
                "summary": "\n".join(part for part in parts if part).strip()[:1000],
                "search_results": profile.get("search_results", []) if isinstance(profile, dict) else [],
                "website_extract": profile.get("website_extract", {}) if isinstance(profile, dict) else {},
            }

        return {"summary": "No additional info found.", "search_results": [], "website_extract": {}}
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
            "reference_patterns": [],
            "anti_patterns": [],
            "design_positioning": "",
        }


def _should_upgrade_research_pass(data: dict) -> bool:
    """Reserve Claude for leads where the local pass looks weak or especially promising."""
    summary = str(data.get("summary", "") or "").strip()
    hooks = data.get("personalization_hooks") or []
    lead_score = int(data.get("lead_score", 0) or 0)
    return len(summary) < 120 or not hooks or lead_score >= 75


def _extract_structured_business_context(web_info: str, lead: dict | None = None) -> dict:
    """Turn scraped business text into a tiny facts + graph bundle."""
    text = (web_info or "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lower_text = text.lower()

    services = _extract_service_phrases(lines)
    locations = _extract_locations(text, lead)
    signals = _extract_signals(lower_text)
    snippets = lines[:3]

    business_name = (lead or {}).get("business_name", "").strip() or "business"
    nodes = [{"id": "business", "type": "business", "label": business_name}]
    edges = []

    for service in services:
        node_id = f"service:{service}"
        nodes.append({"id": node_id, "type": "service", "label": service})
        edges.append({"source": "business", "target": node_id, "type": "offers"})

    for location in locations:
        node_id = f"location:{location}"
        nodes.append({"id": node_id, "type": "location", "label": location})
        edges.append({"source": "business", "target": node_id, "type": "serves"})

    for signal in signals:
        node_id = f"signal:{signal}"
        nodes.append({"id": node_id, "type": "signal", "label": signal})
        edges.append({"source": "business", "target": node_id, "type": "signals"})

    facts = {
        "business_name": business_name,
        "services": services,
        "locations": locations,
        "signals": signals,
        "evidence": snippets,
    }

    return {"facts": facts, "graph": {"nodes": nodes, "edges": edges}}


def _normalize_research_payload(raw_research: str | dict | None) -> dict:
    """Coerce scrape output into a predictable payload."""
    if isinstance(raw_research, dict):
        website_extract = raw_research.get("website_extract", {})
        summary = (
            str(raw_research.get("summary", "") or "").strip()
            or str(website_extract.get("markdown_excerpt", "") or "").strip()
            or str(website_extract.get("description", "") or "").strip()
        )
        return {
            "summary": summary or "No additional info found.",
            "search_results": raw_research.get("search_results", []) if isinstance(raw_research.get("search_results"), list) else [],
            "website_extract": website_extract if isinstance(website_extract, dict) else {},
        }

    return {
        "summary": str(raw_research or "No additional info found.").strip() or "No additional info found.",
        "search_results": [],
        "website_extract": {},
    }


def _select_reference_sites(search_results: list[dict], lead: dict) -> list[dict]:
    """Pick a small set of inspiration references from live research results."""
    selected: list[dict] = []
    seen: set[str] = set()
    lead_url = str(lead.get("website_url", "") or "").rstrip("/")

    for item in search_results:
        url = str(item.get("url", "") or "").strip().rstrip("/")
        if not url or url == lead_url or url in seen:
            continue
        seen.add(url)
        selected.append(
            {
                "title": str(item.get("title", "") or "").strip() or url,
                "url": url,
                "note": str(item.get("description", "") or "").strip()[:180],
            }
        )
        if len(selected) >= 5:
            break

    return selected


def _build_design_reference_packet(
    *,
    lead: dict,
    reference_sites: list[dict],
    structured_context: dict,
    research_data: dict,
) -> dict:
    """Build a compact design packet stored inside research_facts."""
    facts = structured_context.get("facts", {}) if isinstance(structured_context, dict) else {}
    services = facts.get("services", []) if isinstance(facts.get("services"), list) else []
    signals = facts.get("signals", []) if isinstance(facts.get("signals"), list) else []

    default_patterns = []
    if services:
        default_patterns.append(f"service cards for {services[0]}")
    if signals:
        default_patterns.append(f"trust badges highlighting {signals[0]}")
    default_patterns.extend(["clear hero promise", "proof near primary CTA"])

    return {
        "reference_sites": reference_sites,
        "reference_patterns": research_data.get("reference_patterns") or default_patterns[:4],
        "anti_patterns": research_data.get("anti_patterns") or [
            "generic stock-template layouts",
            "weak CTA hierarchy",
            "copied competitor branding",
        ],
        "design_positioning": research_data.get("design_positioning")
        or _default_design_positioning(lead, signals),
    }


def _default_design_positioning(lead: dict, signals: list[str]) -> str:
    """Fallback positioning when the LLM does not provide one."""
    industry = str(lead.get("industry", "") or "").lower()
    if any(keyword in industry for keyword in ("dental", "medical", "clinic", "legal", "real estate")):
        return "premium editorial trust"
    if signals:
        return "trust-first local service"
    return "clean high-conviction service brand"


def _extract_service_phrases(lines: list[str]) -> list[str]:
    phrases: list[str] = []
    service_markers = (
        "we handle",
        "we offer",
        "we provide",
        "we specialize in",
        "we specialise in",
        "services include",
        "service include",
        "specializing in",
        "specialising in",
    )

    for line in lines:
        lowered = line.lower()
        if not any(marker in lowered for marker in service_markers):
            continue
        candidate = line
        for marker in ("we handle", "we offer", "we provide", "we specialize in", "we specialise in",
                       "services include", "service include", "specializing in", "specialising in"):
            if marker in lowered:
                candidate = re.split(marker, line, flags=re.IGNORECASE, maxsplit=1)[-1]
                break
        for phrase in re.split(r",| and ", candidate):
            clean = _normalize_phrase(phrase)
            if clean and clean not in phrases:
                phrases.append(clean)

    return phrases[:5]


def _extract_locations(text: str, lead: dict | None = None) -> list[str]:
    locations: list[str] = []
    location_patterns = [
        r"\b(?:in|based in|located in|serving)\s+([A-Z][^.\n;]*)",
        r"\b([A-Z][a-zA-Z'&.\- ]+,\s*[A-Z]{2})",
    ]

    for pattern in location_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            for part in re.split(r",| and ", match):
                clean = _normalize_phrase(part)
                if clean and clean not in locations:
                    locations.append(clean)

    for fallback_key in ("city", "country"):
        value = str((lead or {}).get(fallback_key, "") or "").strip()
        if value and value not in locations:
            locations.append(value)

    return locations[:6]


def _extract_signals(lower_text: str) -> list[str]:
    signals = []
    signal_patterns = [
        ("family-owned", "family-owned"),
        ("same-day", "same-day service"),
        ("24/7", "24/7 availability"),
        ("emergency", "emergency service"),
        ("local", "local business"),
        ("licensed", "licensed"),
        ("insured", "insured"),
    ]

    for needle, label in signal_patterns:
        if needle in lower_text and label not in signals:
            signals.append(label)

    return signals[:5]


def _normalize_phrase(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip(" .,-;:")
    clean = re.sub(
        r"^(?:emergency|same-day|same day|24/7|local|family-owned|commercial|residential)\s+",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"^(?:the|a|an)\s+", "", clean, flags=re.IGNORECASE)
    return clean
