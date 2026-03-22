"""Tests for curated design source resolution."""

import importlib


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
