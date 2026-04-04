"""Tests for Apify and Tavily integration into Titan pipeline stages."""

import asyncio
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_APIFY_LEADS = [
    {
        "name": "Joe's Plumbing",
        "phone": "+1-555-0101",
        "email": "",
        "website": "http://joesplumbing.example.com",
        "address": "123 Main St",
        "city": "Austin",
        "state": "TX",
        "category": "Plumber",
        "rating": 4.5,
        "reviews_count": 42,
        "latitude": 30.27,
        "longitude": -97.74,
        "source": "apify",
        "source_url": "https://maps.google.com/?cid=123",
        "place_id": "ChIJ_test",
    },
    {
        "name": "Maria's Bakery",
        "phone": "+1-555-0102",
        "email": "maria@bakery.example.com",
        "website": "",
        "address": "456 Oak Ave",
        "city": "Austin",
        "state": "TX",
        "category": "Bakery",
        "rating": 4.8,
        "reviews_count": 100,
        "latitude": 30.28,
        "longitude": -97.75,
        "source": "apify",
        "source_url": "https://maps.google.com/?cid=456",
        "place_id": "ChIJ_test2",
    },
]

SAMPLE_TAVILY_ENRICHMENT = {
    "business_name": "Joe's Plumbing",
    "location": "Austin",
    "website_info": "Joe's Plumbing provides residential plumbing services.",
    "reviews_summary": "Rated 4.5 stars on Google with 42 reviews.",
    "competitors": [
        {"name": "ABC Plumbing", "url": "https://abcplumbing.example.com", "snippet": "Competitor plumber"},
    ],
    "social_media": ["https://facebook.com/joesplumbing"],
    "has_website": True,
    "overall_summary": "Joe's Plumbing (has a website, 1 social profiles, 1 competitors found).",
    "sources": ["https://joesplumbing.example.com", "https://yelp.com/joe"],
}


def _stub_heavy_modules():
    """Provide lightweight stubs for modules with heavy native dependencies so
    that titan.pipeline.lead_discovery can be imported without installing
    everything (cryptography, psycopg2, etc.)."""
    stubs = {}
    # Modules that lead_discovery (and its transitive imports) need at import
    # time but are not relevant for these unit-level integration tests.
    for mod_name in [
        "cryptography", "cryptography.exceptions", "cryptography.hazmat",
        "cryptography.hazmat.primitives", "cryptography.hazmat.primitives.asymmetric",
        "cryptography.hazmat.primitives.asymmetric.padding",
        "cryptography.hazmat.primitives.hashes",
        "cryptography.hazmat.primitives.serialization",
        "psycopg2", "psycopg2.extras", "psycopg2.pool",
    ]:
        if mod_name not in sys.modules:
            stubs[mod_name] = types.ModuleType(mod_name)
            # Provide the names that shared/skill_loader.py expects
            if mod_name == "cryptography.exceptions":
                stubs[mod_name].InvalidSignature = type("InvalidSignature", (Exception,), {})
            sys.modules[mod_name] = stubs[mod_name]
    return stubs


# Install stubs before importing any titan modules
_STUBS = _stub_heavy_modules()


# ---------------------------------------------------------------------------
# 1. Discovery uses Apify when available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discovery_uses_apify_when_available():
    """When APIFY_API_TOKEN is set and Apify returns leads, skill-based discovery is skipped."""
    stored_ids = iter(range(1, 100))

    async def fake_store_lead(biz):
        return next(stored_ids)

    async def fake_apify_discover(query, location, max_results):
        return SAMPLE_APIFY_LEADS[:max_results]

    async def fake_enrich_emails(leads):
        return leads

    async def fake_apify_available():
        return True

    with patch("titan.pipeline.lead_discovery.HAS_APIFY", True), \
         patch("titan.pipeline.lead_discovery.apify_available", fake_apify_available), \
         patch("titan.pipeline.lead_discovery.apify_discover", fake_apify_discover), \
         patch("titan.pipeline.lead_discovery.enrich_lead_emails", fake_enrich_emails), \
         patch("titan.pipeline.lead_discovery._store_lead", fake_store_lead), \
         patch("titan.pipeline.lead_discovery._get_discovery_strategy", AsyncMock(return_value={
             "search_queries": ["plumber no website"],
             "target_industries": ["plumber"],
             "target_regions": ["Austin"],
             "reasoning": "test",
         })), \
         patch("titan.pipeline.lead_discovery.emit_event", AsyncMock()):

        from titan.pipeline.lead_discovery import discover_leads
        result = await discover_leads(batch_size=5)

    assert len(result) > 0, "Apify should have produced leads"


# ---------------------------------------------------------------------------
# 2. Discovery falls back without Apify
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discovery_falls_back_without_apify():
    """When HAS_APIFY is False, the pipeline falls through to skill-based discovery."""
    with patch("titan.pipeline.lead_discovery.HAS_APIFY", False), \
         patch("titan.pipeline.lead_discovery._get_discovery_strategy", AsyncMock(return_value={
             "search_queries": ["plumber no website"],
             "target_industries": ["plumber"],
             "target_regions": ["Austin"],
             "reasoning": "test",
         })), \
         patch("titan.pipeline.lead_discovery._choose_discovery_sources", AsyncMock(return_value={
             "source_order": ["custom_firecrawl"],
             "reasoning": "fallback",
         })), \
         patch("titan.pipeline.lead_discovery._apply_discovery_overrides", AsyncMock(return_value={
             "source_order": ["custom_firecrawl"],
             "reasoning": "fallback",
         })), \
         patch("titan.pipeline.lead_discovery._discover_custom", AsyncMock(return_value=[101, 102])):

        from titan.pipeline.lead_discovery import discover_leads
        result = await discover_leads(batch_size=5)

    assert result == [101, 102], "Should fall through to custom_firecrawl discovery"


# ---------------------------------------------------------------------------
# 3. Research uses Tavily enrichment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_uses_tavily_enrichment():
    """When Tavily is available, its enrichment data is merged into the research payload."""
    async def fake_tavily_research(business_name, location=""):
        return SAMPLE_TAVILY_ENRICHMENT

    with patch("titan.pipeline.lead_research.HAS_TAVILY", True), \
         patch("titan.pipeline.lead_research.tavily_available", lambda: True), \
         patch("titan.pipeline.lead_research.tavily_research", fake_tavily_research):

        from titan.pipeline.lead_research import _enrich_with_tavily
        lead = {"id": 1, "business_name": "Joe's Plumbing", "city": "Austin", "country": "US"}
        result = await _enrich_with_tavily(lead)

    assert result is not None, "Tavily enrichment should return data"
    assert result["overall_summary"], "Should have an overall_summary"
    assert len(result["competitors"]) > 0, "Should have competitors"
    assert len(result["sources"]) > 0, "Should have sources"


# ---------------------------------------------------------------------------
# 4. Research works without Tavily
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_works_without_tavily():
    """When HAS_TAVILY is False, _enrich_with_tavily returns None gracefully."""
    with patch("titan.pipeline.lead_research.HAS_TAVILY", False):
        from titan.pipeline.lead_research import _enrich_with_tavily
        lead = {"id": 1, "business_name": "Joe's Plumbing", "city": "Austin", "country": "US"}
        result = await _enrich_with_tavily(lead)

    assert result is None, "Should return None when Tavily is unavailable"


# ---------------------------------------------------------------------------
# 5. Apify lead format conversion
# ---------------------------------------------------------------------------

def test_apify_lead_format_conversion():
    """Apify's raw lead format converts correctly to the pipeline's expected format."""
    from titan.pipeline.lead_discovery import _convert_apify_lead

    apify_lead = SAMPLE_APIFY_LEADS[0]
    converted = _convert_apify_lead(apify_lead)

    assert converted["business_name"] == "Joe's Plumbing"
    assert converted["phone"] == "+1-555-0101"
    assert converted["email"] == ""
    assert converted["industry"] == "Plumber"
    assert converted["website_url"] == "http://joesplumbing.example.com"
    assert converted["city"] == "Austin"
    assert converted["source"] == "apify"

    # Test lead with no city but state present
    apify_lead_no_city = {**SAMPLE_APIFY_LEADS[1], "city": ""}
    converted2 = _convert_apify_lead(apify_lead_no_city)
    assert converted2["city"] == "TX", "Should fall back to state when city is empty"
