"""Behavior tests for structured lead research extraction."""

import asyncio
import importlib
import json
import sys
import types
from unittest.mock import AsyncMock

import pytest


def run(coro):
    return asyncio.run(coro)


def load_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()

    fake_comms = types.ModuleType("shared.comms")
    fake_comms.request_task_result = AsyncMock()

    fake_llm = types.ModuleType("shared.llm_client")
    fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"summary":"ok"}'))

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    sys.modules.pop("titan.pipeline.lead_research", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["shared.comms"] = fake_comms
    sys.modules["shared.llm_client"] = fake_llm
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state_machine
    return importlib.import_module("titan.pipeline.lead_research")


def test_structured_extraction_turns_scraped_text_into_facts_and_graph():
    module = load_module()

    structured = module._extract_structured_business_context(
        """North Shore Plumbing is a family-owned plumber in Austin, TX.
We handle emergency drain cleaning, water heater repair, and leak detection.
Serving Austin, Round Rock, and nearby communities.
Call us at 555-0100 for same-day service."""
    )

    assert "Austin" in structured["facts"]["locations"]
    assert "drain cleaning" in structured["facts"]["services"]
    assert structured["graph"]["nodes"]
    assert structured["graph"]["edges"]


@pytest.mark.asyncio
async def test_research_one_stores_structured_context_and_uses_it_in_prompt():
    module = load_module()
    prompt_capture = {}
    execute_capture = {}

    async def fake_scrape(_lead):
        return """North Shore Plumbing is a family-owned plumber in Austin, TX.
We handle emergency drain cleaning, water heater repair, and leak detection.
Serving Austin, Round Rock, and nearby communities."""

    async def fake_generate(prompt, lead):
        prompt_capture["prompt"] = prompt
        prompt_capture["lead"] = lead
        return {
            "summary": "ok",
            "lead_score": 77,
            "personalization_hooks": ["fast response"],
            "estimated_industry": "plumbing",
            "language": "en",
        }

    async def fake_execute(query, params=()):
        execute_capture["query"] = query
        execute_capture["params"] = params

    module._scrape_business_info = fake_scrape
    module._generate_research_data = fake_generate
    module.execute = fake_execute
    module.transition_lead = AsyncMock()

    lead = {
        "id": 9,
        "business_name": "North Shore Plumbing",
        "email": "hello@example.com",
        "industry": "home services",
        "website_url": "https://example.com",
        "city": "Austin",
        "country": "US",
    }

    await module._research_one(lead)

    assert "Structured facts" in prompt_capture["prompt"]
    assert "drain cleaning" in prompt_capture["prompt"]
    assert "research_facts" in execute_capture["query"]
    assert "research_graph" in execute_capture["query"]
    stored_facts = json.loads(execute_capture["params"][4])
    stored_graph = json.loads(execute_capture["params"][5])
    assert "drain cleaning" in stored_facts["services"]
    assert stored_graph["nodes"]


@pytest.mark.asyncio
async def test_research_one_stores_design_reference_packet_in_research_facts():
    module = load_module()
    execute_capture = {}
    prompt_capture = {}

    async def fake_scrape(_lead):
        return {
            "summary": "Premium dental clinic with strong cosmetic positioning.",
            "search_results": [
                {
                    "title": "Studio Dental",
                    "url": "https://studio.example",
                    "description": "Premium dental studio with polished hero and clear CTA",
                },
                {
                    "title": "North Coast Dental",
                    "url": "https://northcoast.example",
                    "description": "Trust-heavy dental site with social proof",
                },
            ],
        }

    async def fake_generate(prompt, lead):
        prompt_capture["prompt"] = prompt
        return {
            "summary": "ok",
            "lead_score": 81,
            "personalization_hooks": ["cosmetic dentistry focus"],
            "estimated_industry": "dentistry",
            "language": "en",
            "reference_patterns": ["split hero", "before-after proof"],
            "anti_patterns": ["generic smiling stock photos"],
            "design_positioning": "premium editorial clinic",
        }

    async def fake_execute(query, params=()):
        execute_capture["query"] = query
        execute_capture["params"] = params

    module._scrape_business_info = fake_scrape
    module._generate_research_data = fake_generate
    module.execute = fake_execute
    module.transition_lead = AsyncMock()

    lead = {
        "id": 14,
        "business_name": "Atlas Dental",
        "email": "hello@example.com",
        "industry": "dental",
        "website_url": "https://atlas.example",
        "city": "Mazatlan",
        "country": "MX",
    }

    await module._research_one(lead)

    assert "Candidate inspiration sites" in prompt_capture["prompt"]
    stored_facts = json.loads(execute_capture["params"][4])
    assert stored_facts["reference_sites"][0]["url"] == "https://studio.example"
    assert "split hero" in stored_facts["reference_patterns"]
    assert stored_facts["design_positioning"] == "premium editorial clinic"
