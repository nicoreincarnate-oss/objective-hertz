"""
Email Enrichment — Data injection + entity-structured context for email composition.

Papers: Tailored Truths (Paper 67, 51% vs 32% opinion change with data points),
RAGMail (Paper 64, entity-structured cold email generation).

Two capabilities:
1. Data point injection: verified industry statistics in email prompts
2. Entity context: MAGMA entity graph provides structured business context

Gated behind DATA_INJECTION_ENABLED=1 and ENTITY_EMAIL_ENABLED=1.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.email_enrichment")

DATA_INJECTION_ENABLED = os.environ.get("DATA_INJECTION_ENABLED", "1") == "1"
ENTITY_EMAIL_ENABLED = os.environ.get("ENTITY_EMAIL_ENABLED", "1") == "1"


async def inject_industry_stats(industry: str, region: str = "") -> str:
    """Retrieve verified industry statistics for email personalization.

    Tailored Truths paper: personalization + data points → 51% opinion change
    vs 32% for static human-written content.

    Returns formatted stats block for email prompt injection.
    NEVER fabricates — only returns verified or clearly tagged data.
    """
    if not DATA_INJECTION_ENABLED:
        return ""

    stats = []

    # Source 1: MAGMA entity graph (verified stats stored as IndustryStat nodes)
    try:
        from shared.magma import _get_driver
        driver = _get_driver()
        if driver:
            with driver.session() as session:
                results = session.run(
                    """MATCH (s:IndustryStat)
                       WHERE toLower(s.industry) = toLower($industry)
                       RETURN s.stat_text as stat, s.source_url as source, s.verified_at as verified
                       ORDER BY s.verified_at DESC LIMIT 3""",
                    industry=industry,
                )
                for r in results:
                    stats.append(f"- {r['stat']} [source: {r.get('source', 'internal')}]")
    except Exception:
        pass

    # Source 2: Titan learnings about this industry
    try:
        from shared.db import fetch_all
        learnings = await fetch_all(
            """SELECT insight FROM titan_learnings
               WHERE category = 'industry' AND confidence > 0.7
               AND LOWER(insight) LIKE %s
               ORDER BY confidence DESC LIMIT 2""",
            (f"%{industry.lower()}%",),
        )
        for l in learnings:
            stats.append(f"- {l['insight']} [source: internal analytics]")
    except Exception:
        pass

    if not stats:
        # Source 3: LLM-generated (tagged as unverified)
        try:
            from shared.llm_client import llm
            result = await llm.generate(
                f"Give 2 specific, realistic statistics about the {industry} industry"
                f"{f' in {region}' if region else ''}. "
                f"Format: one stat per line, with specific numbers.",
                model="fast",
                temperature=0.3,
                max_tokens=200,
            )
            for line in result.strip().split("\n"):
                if line.strip():
                    stats.append(f"- {line.strip()} [UNVERIFIED — use cautiously]")
        except Exception:
            pass

    if not stats:
        return ""

    return (
        "INDUSTRY DATA POINTS (cite ONE in your email for credibility — "
        "only use VERIFIED stats unless explicitly told otherwise):\n"
        + "\n".join(stats[:3])
        + "\n\n"
    )


async def get_entity_context(business_name: str, client_id: int | None = None) -> str:
    """Retrieve entity-structured context from MAGMA graph.

    RAGMail paper: entity-relationship retrieval outperforms flat context
    for cold email personalization.

    Returns formatted context block for email prompt injection.
    """
    if not ENTITY_EMAIL_ENABLED:
        return ""

    context_parts = []

    # Query MAGMA entity graph
    try:
        from shared.magma import _get_driver
        driver = _get_driver()
        if driver:
            with driver.session() as session:
                # Get all events related to this business
                results = session.run(
                    """MATCH (e:Entity)<-[:INVOLVES]-(n:MemoryNode)
                       WHERE toLower(e.name) = toLower($name)
                       RETURN n.content as content, n.category as category, n.timestamp as ts
                       ORDER BY n.timestamp DESC LIMIT 5""",
                    name=business_name,
                )
                for r in results:
                    context_parts.append(f"[{r.get('category', '?')}] {r.get('content', '')[:150]}")

                # Get connected entities (related businesses, industries)
                related = session.run(
                    """MATCH (e:Entity {name: $name})<-[:INVOLVES]-(n:MemoryNode)-[:INVOLVES]->(other:Entity)
                       WHERE other.name <> $name
                       RETURN DISTINCT other.name as related, other.entity_type as type
                       LIMIT 5""",
                    name=business_name.lower(),
                )
                related_list = [f"{r['related']} ({r.get('type', 'entity')})" for r in related]
                if related_list:
                    context_parts.append(f"Related: {', '.join(related_list)}")
    except Exception:
        pass

    # Fallback: use research_facts from clients table
    if not context_parts and client_id:
        try:
            from shared.db import fetch_one
            client = await fetch_one(
                "SELECT research_facts, industry, region FROM clients WHERE id = %s",
                (client_id,),
            )
            if client:
                facts = client.get("research_facts", {})
                if isinstance(facts, str):
                    import json
                    try:
                        facts = json.loads(facts)
                    except Exception:
                        facts = {}
                if facts:
                    context_parts.append(f"Industry: {client.get('industry', 'unknown')}")
                    context_parts.append(f"Region: {client.get('region', 'unknown')}")
                    for k, v in list(facts.items())[:5]:
                        context_parts.append(f"{k}: {v}")
        except Exception:
            pass

    if not context_parts:
        return ""

    return (
        "ENTITY CONTEXT (structured business intelligence — use to personalize):\n"
        + "\n".join(f"  {p}" for p in context_parts)
        + "\n\n"
    )


async def enrich_email_prompt(
    base_prompt: str,
    industry: str = "",
    region: str = "",
    business_name: str = "",
    client_id: int | None = None,
) -> str:
    """One-call enrichment: inject industry stats + entity context into prompt.

    Drop-in enhancement for email_compose — call before LLM generation.
    """
    enrichments = []

    if DATA_INJECTION_ENABLED and industry:
        stats = await inject_industry_stats(industry, region)
        if stats:
            enrichments.append(stats)

    if ENTITY_EMAIL_ENABLED and business_name:
        context = await get_entity_context(business_name, client_id)
        if context:
            enrichments.append(context)

    if enrichments:
        return "\n".join(enrichments) + base_prompt
    return base_prompt
