"""Tests for Apify lead generation client."""

import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch

from tools.apify_client import (
    apify_available,
    discover_leads,
    enrich_lead_emails,
    _normalize_leads,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_apify_items():
    """Simulated raw output from compass/crawler-google-places."""
    return [
        {
            "title": "Joe's Plumbing",
            "phone": "+15551234567",
            "website": "https://joesplumbing.com",
            "address": "123 Main St",
            "city": "Austin",
            "state": "TX",
            "categoryName": "Plumber",
            "totalScore": 4.5,
            "reviewsCount": 120,
            "location": {"lat": 30.2672, "lng": -97.7431},
            "url": "https://maps.google.com/?cid=123",
            "placeId": "ChIJ_abc123",
        },
        {
            "title": "Best HVAC",
            "phone": "+15559876543",
            "website": "https://besthvac.com",
            "address": "456 Oak Ave",
            "city": "Austin",
            "state": "TX",
            "categoryName": "HVAC Contractor",
            "totalScore": 4.8,
            "reviewsCount": 89,
            "location": {"lat": 30.3, "lng": -97.8},
            "url": "https://maps.google.com/?cid=456",
            "placeId": "ChIJ_def456",
        },
        {
            # Entry with no title — should be filtered out
            "title": "",
            "phone": "",
            "website": "",
        },
    ]


# ---------------------------------------------------------------------------
# apify_available
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apify_available_with_token(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token-123")
    assert await apify_available() is True


@pytest.mark.asyncio
async def test_apify_available_without_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    assert await apify_available() is False


# ---------------------------------------------------------------------------
# discover_leads — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_leads_success(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token-123")

    items = _fake_apify_items()

    # Build mock chain: client.actor(id).call() -> run dict
    mock_run = {"status": "SUCCEEDED", "defaultDatasetId": "ds-001"}
    mock_actor_instance = MagicMock()
    mock_actor_instance.call = AsyncMock(return_value=mock_run)

    mock_dataset_result = MagicMock()
    mock_dataset_result.items = items
    mock_dataset_instance = MagicMock()
    mock_dataset_instance.list_items = AsyncMock(return_value=mock_dataset_result)

    mock_client = MagicMock()
    mock_client.actor = MagicMock(return_value=mock_actor_instance)
    mock_client.dataset = MagicMock(return_value=mock_dataset_instance)
    mock_client.close = AsyncMock()

    fake_module = MagicMock()
    fake_module.ApifyClientAsync = MagicMock(return_value=mock_client)
    with patch.dict(sys.modules, {"apify_client": fake_module}):
        leads = await discover_leads("plumber", "Austin TX", max_results=10)

    assert len(leads) == 2  # Empty-title entry filtered
    assert leads[0]["name"] == "Joe's Plumbing"
    assert leads[0]["source"] == "apify"
    assert leads[1]["name"] == "Best HVAC"


# ---------------------------------------------------------------------------
# discover_leads — no token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_leads_no_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    leads = await discover_leads("plumber", "Austin TX")
    assert leads == []


# ---------------------------------------------------------------------------
# discover_leads — import error (apify-client not installed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_leads_import_error(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token-123")

    # Force ImportError by removing apify_client from sys.modules
    with patch.dict(sys.modules, {"apify_client": None}):
        leads = await discover_leads("plumber", "Austin TX")
    assert leads == []


# ---------------------------------------------------------------------------
# discover_leads — API error (graceful degradation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_leads_api_error(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token-123")

    mock_actor_instance = MagicMock()
    mock_actor_instance.call = AsyncMock(side_effect=RuntimeError("API timeout"))

    mock_client = MagicMock()
    mock_client.actor = MagicMock(return_value=mock_actor_instance)
    mock_client.close = AsyncMock()

    fake_module = MagicMock()
    fake_module.ApifyClientAsync = MagicMock(return_value=mock_client)
    with patch.dict(sys.modules, {"apify_client": fake_module}):
        leads = await discover_leads("plumber", "Austin TX")

    assert leads == []


# ---------------------------------------------------------------------------
# enrich_lead_emails
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_lead_emails(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token-123")

    input_leads = [
        {"name": "Joe's Plumbing", "website": "https://joesplumbing.com", "email": ""},
        {"name": "Best HVAC", "website": "https://besthvac.com", "email": ""},
        {"name": "No Website Co", "website": "", "email": ""},
    ]

    enrichment_items = [
        {"website": "https://joesplumbing.com", "emails": ["joe@joesplumbing.com"]},
        {"url": "https://besthvac.com", "emails": ["info@besthvac.com"]},
    ]

    mock_run = {"status": "SUCCEEDED", "defaultDatasetId": "ds-enrich"}
    mock_actor_instance = MagicMock()
    mock_actor_instance.call = AsyncMock(return_value=mock_run)

    mock_dataset_result = MagicMock()
    mock_dataset_result.items = enrichment_items
    mock_dataset_instance = MagicMock()
    mock_dataset_instance.list_items = AsyncMock(return_value=mock_dataset_result)

    mock_client = MagicMock()
    mock_client.actor = MagicMock(return_value=mock_actor_instance)
    mock_client.dataset = MagicMock(return_value=mock_dataset_instance)
    mock_client.close = AsyncMock()

    fake_module = MagicMock()
    fake_module.ApifyClientAsync = MagicMock(return_value=mock_client)
    with patch.dict(sys.modules, {"apify_client": fake_module}):
        enriched = await enrich_lead_emails(input_leads)

    assert enriched[0]["email"] == "joe@joesplumbing.com"
    # Second item uses "url" key — enrichment falls back to url via `or`
    assert enriched[1]["email"] == "info@besthvac.com"
    assert enriched[2]["email"] == ""  # No website, no enrichment


# ---------------------------------------------------------------------------
# Lead normalization
# ---------------------------------------------------------------------------

def test_lead_normalization():
    items = _fake_apify_items()
    leads = _normalize_leads(items)

    assert len(leads) == 2  # Empty-title entry filtered out

    lead = leads[0]
    expected_keys = {
        "name", "phone", "email", "website", "address",
        "city", "state", "category", "rating", "reviews_count",
        "latitude", "longitude", "source", "source_url", "place_id",
    }
    assert set(lead.keys()) == expected_keys
    assert lead["name"] == "Joe's Plumbing"
    assert lead["phone"] == "+15551234567"
    assert lead["email"] == ""
    assert lead["website"] == "https://joesplumbing.com"
    assert lead["city"] == "Austin"
    assert lead["state"] == "TX"
    assert lead["category"] == "Plumber"
    assert lead["rating"] == 4.5
    assert lead["reviews_count"] == 120
    assert lead["latitude"] == 30.2672
    assert lead["longitude"] == -97.7431
    assert lead["source"] == "apify"
    assert lead["place_id"] == "ChIJ_abc123"
