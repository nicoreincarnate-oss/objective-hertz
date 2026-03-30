"""Curated design source adapter for site generation."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("perseus.clawdbot.design_sources")

_CURATED_SOURCES = [
    {
        "source": "21st.dev",
        "title": "Split Hero CTA",
        "slug": "split-hero-cta",
        "industries": {"service", "dentist", "dental", "clinic", "medical", "legal"},
        "sections": ["hero", "proof", "cta"],
        "why": "Strong hero framing with immediate CTA and room for proof beside the fold.",
    },
    {
        "source": "21st.dev",
        "title": "Proof-First Testimonial Stack",
        "slug": "proof-first-stack",
        "industries": {"service", "dentist", "restaurant", "plumber", "clinic"},
        "sections": ["testimonials", "results", "cta"],
        "why": "Lets the page earn trust before asking for the conversion.",
    },
    {
        "source": "stitch",
        "title": "Premium Service Editorial",
        "slug": "premium-service-editorial",
        "industries": {"dentist", "dental", "clinic", "medical", "legal", "real estate"},
        "sections": ["hero", "services", "proof"],
        "why": "High-trust editorial direction for premium local-service positioning.",
    },
    {
        "source": "stitch",
        "title": "Local Service High-Trust Flow",
        "slug": "local-service-high-trust",
        "industries": {"service", "plumber", "home services", "hvac", "electrician"},
        "sections": ["hero", "badges", "services", "contact"],
        "why": "Fast-scanning service funnel with trust signals and direct response flow.",
    },
]


def resolve_design_sources(lead: dict[str, Any]) -> dict[str, Any]:
    """Resolve structured design sources from curated catalog plus live references."""
    industry = str(lead.get("industry", "") or "").lower()
    research_facts = _extract_research_facts(lead)
    reference_sites = research_facts.get("reference_sites", [])

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(source: dict[str, Any]) -> None:
        key = f"{source.get('source', '')}:{source.get('slug') or source.get('url') or source.get('title', '')}"
        if key in seen:
            return
        seen.add(key)
        sources.append(source)

    for source in _CURATED_SOURCES:
        industries = source.get("industries", set())
        if industry and industry in industries:
            add({k: v for k, v in source.items() if k != "industries"})

    if not sources:
        for source in _CURATED_SOURCES:
            if "service" in source.get("industries", set()):
                add({k: v for k, v in source.items() if k != "industries"})

    for site in reference_sites:
        if not isinstance(site, dict):
            continue
        url = str(site.get("url", "") or "").strip()
        if not url:
            continue
        add(
            {
                "source": "web_reference",
                "title": str(site.get("title", "") or "").strip() or url,
                "url": url,
                "sections": [],
                "why": str(site.get("note", "") or "").strip(),
            }
        )

    adaptation_rules = [
        "Use shadcn/ui as the production system spine even when adapting outside inspiration.",
        "Treat 21st.dev sources as section-pattern inspiration, not copy targets.",
        "Treat Stitch sources as art direction or layout prototypes, not production code.",
        "Never copy third-party branding, assets, or exact markup.",
    ]

    return {"sources": sources, "adaptation_rules": adaptation_rules}


def _extract_research_facts(lead: dict[str, Any]) -> dict[str, Any]:
    raw = lead.get("research_facts")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Async component enrichment (21st.dev REST API)
# ---------------------------------------------------------------------------


async def _fetch_component(message: str, search_query: str) -> dict[str, Any]:
    """Fetch a component snippet from 21st.dev. Thin wrapper for mockability."""
    try:
        from tools.twentyfirst_client import fetch_component_inspiration
    except Exception:
        return {"text": "", "search_query": search_query, "reason": "import_failed"}
    return await fetch_component_inspiration(message=message, search_query=search_query)


async def resolve_design_sources_with_components(
    lead: dict[str, Any],
) -> dict[str, Any]:
    """Resolve design sources AND fetch actual component code from 21st.dev.

    Enriches 21st.dev curated sources with real component snippets for use as
    structural inspiration. Falls back to base (text-only) behavior on any error.
    """
    base = resolve_design_sources(lead)

    try:
        industry = str(lead.get("industry", "") or "").lower()

        for source in base["sources"]:
            if source.get("source") != "21st.dev":
                continue

            sections = source.get("sections", [])
            for section in sections[:2]:  # max 2 fetches per source
                snippet = await _fetch_component(
                    message=f"{section} section for {industry} business website",
                    search_query=f"{section} {source.get('title', '')}",
                )
                text = snippet.get("text", "")
                if text:
                    source.setdefault("component_snippets", []).append(
                        {"section": section, "code": text[:1500]}
                    )

        # Add React/TSX adaptation rule
        tsx_rule = (
            "Component code from 21st.dev is React/TSX -- use as structural "
            "inspiration for HTML+Tailwind, never copy JSX syntax directly."
        )
        if tsx_rule not in base["adaptation_rules"]:
            base["adaptation_rules"].append(tsx_rule)

    except Exception as e:
        logger.warning("Component enrichment failed, returning base sources: %s", e)

    return base
