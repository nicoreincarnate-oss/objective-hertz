"""Tests for curated design source resolution."""

import importlib
from unittest.mock import AsyncMock, patch

import pytest


def load_module():
    return importlib.import_module("clawdbot.design_sources")


def test_resolve_design_sources_merges_catalog_and_research_references():
    module = load_module()

    lead = {
        "industry": "dentist",
        "research_facts": {
            "reference_sites": [
                {
                    "title": "Studio Dental",
                    "url": "https://studio.example",
                    "note": "Premium hero and trust rail",
                }
            ]
        },
    }

    payload = module.resolve_design_sources(lead)

    sources = payload["sources"]
    assert any(source["source"] == "21st.dev" for source in sources)
    assert any(source["source"] == "stitch" for source in sources)
    assert any(source["source"] == "web_reference" for source in sources)
    assert payload["adaptation_rules"]


# --- Test 1: resolve_design_sources_with_components adds component_snippets ---


@pytest.mark.asyncio
async def test_with_components_adds_component_snippets_when_api_returns_data():
    module = load_module()

    lead = {
        "industry": "dentist",
        "research_facts": {},
    }

    fake_snippet = {
        "text": "function SplitHero() { return <div className='hero'>...</div> }",
        "search_query": "hero Split Hero CTA",
    }

    mock_fetch = AsyncMock(return_value=fake_snippet)

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    sources_21st = [s for s in result["sources"] if s.get("source") == "21st.dev"]
    assert len(sources_21st) > 0

    has_snippets = any("component_snippets" in s for s in sources_21st)
    assert has_snippets, "Expected at least one 21st.dev source to have component_snippets"

    for s in sources_21st:
        for snippet in s.get("component_snippets", []):
            assert "section" in snippet
            assert "code" in snippet


# --- Test 2: same structure as resolve_design_sources when API unavailable ---


@pytest.mark.asyncio
async def test_with_components_returns_same_structure_when_api_unavailable():
    module = load_module()

    lead = {
        "industry": "dentist",
        "research_facts": {},
    }

    mock_fetch = AsyncMock(return_value={"text": "", "search_query": "", "reason": "unavailable"})

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    assert "sources" in result
    assert "adaptation_rules" in result


# --- Test 3: component_snippets have section and code keys ---


@pytest.mark.asyncio
async def test_component_snippets_have_section_and_code_keys():
    module = load_module()

    lead = {"industry": "dentist", "research_facts": {}}

    fake_snippet = {"text": "<div>Component code here</div>", "search_query": "hero"}

    mock_fetch = AsyncMock(return_value=fake_snippet)

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    for source in result["sources"]:
        for snippet in source.get("component_snippets", []):
            assert "section" in snippet
            assert "code" in snippet


# --- Test 4: component snippet code truncated to max 1500 chars ---


@pytest.mark.asyncio
async def test_component_snippet_code_truncated_to_1500_chars():
    module = load_module()

    lead = {"industry": "dentist", "research_facts": {}}

    long_text = "X" * 3000
    fake_snippet = {"text": long_text, "search_query": "hero"}

    mock_fetch = AsyncMock(return_value=fake_snippet)

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    for source in result["sources"]:
        for snippet in source.get("component_snippets", []):
            assert len(snippet["code"]) <= 1500


# --- Test 5: max 2 component fetches per curated source ---


@pytest.mark.asyncio
async def test_max_two_component_fetches_per_source():
    module = load_module()

    lead = {"industry": "dentist", "research_facts": {}}

    fake_snippet = {"text": "component code", "search_query": "section"}

    mock_fetch = AsyncMock(return_value=fake_snippet)

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    for source in result["sources"]:
        if source.get("source") == "21st.dev":
            snippets = source.get("component_snippets", [])
            assert len(snippets) <= 2


# --- Test 6: adaptation_rules still present in enriched output ---


@pytest.mark.asyncio
async def test_adaptation_rules_present_in_enriched_output():
    module = load_module()

    lead = {"industry": "dentist", "research_facts": {}}

    mock_fetch = AsyncMock(return_value={"text": "code", "search_query": "hero"})

    with patch.object(module, "_fetch_component", mock_fetch):
        result = await module.resolve_design_sources_with_components(lead)

    assert "adaptation_rules" in result
    assert len(result["adaptation_rules"]) > 0
    # Should include the React/TSX adaptation rule
    rules_text = " ".join(result["adaptation_rules"])
    assert "React" in rules_text or "TSX" in rules_text or "structural inspiration" in rules_text


# --- Test 7: existing resolve_design_sources unchanged ---


def test_resolve_design_sources_unchanged_after_enrichment_additions():
    """Verify the base function still works identically."""
    module = load_module()

    lead = {"industry": "plumber", "research_facts": {}}

    payload = module.resolve_design_sources(lead)

    assert "sources" in payload
    assert "adaptation_rules" in payload
    assert any(s["source"] == "21st.dev" for s in payload["sources"])
    # No component_snippets in base function
    for source in payload["sources"]:
        assert "component_snippets" not in source
