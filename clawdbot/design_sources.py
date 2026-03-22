"""Curated design source adapter for site generation."""

from __future__ import annotations

import json
from typing import Any


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
