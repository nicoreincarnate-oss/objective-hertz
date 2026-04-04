"""Tests for Tavily search client."""

import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# tavily_available
# ---------------------------------------------------------------------------


def test_tavily_available_with_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")
    from tools.tavily_client import tavily_available

    assert tavily_available() is True


def test_tavily_available_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    from tools.tavily_client import tavily_available

    assert tavily_available() is False


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_success(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    mock_response = {
        "results": [
            {
                "title": "Acme Plumbing",
                "url": "https://acmeplumbing.com",
                "content": "Acme Plumbing offers residential services.",
                "score": 0.95,
                "raw_content": "Full page content here.",
            },
            {
                "title": "Acme Plumbing Reviews",
                "url": "https://yelp.com/acme-plumbing",
                "content": "4.5 stars on Yelp.",
                "score": 0.80,
                "raw_content": "",
            },
        ]
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.search = AsyncMock(return_value=mock_response)
    mock_async_class = MagicMock(return_value=mock_client_instance)

    # Create a fake tavily module with AsyncTavilyClient
    fake_tavily = MagicMock()
    fake_tavily.AsyncTavilyClient = mock_async_class
    monkeypatch.setitem(sys.modules, "tavily", fake_tavily)

    import importlib
    import tools.tavily_client as tc
    importlib.reload(tc)

    results = await tc.search("Acme Plumbing", max_results=2)

    assert len(results) == 2
    assert results[0]["title"] == "Acme Plumbing"
    assert results[0]["url"] == "https://acmeplumbing.com"
    assert results[0]["score"] == 0.95
    assert results[1]["title"] == "Acme Plumbing Reviews"

    # Clean up
    monkeypatch.delitem(sys.modules, "tavily", raising=False)


@pytest.mark.asyncio
async def test_search_no_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    from tools.tavily_client import search

    results = await search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_search_import_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    # Remove tavily from sys.modules so the import inside search() triggers
    monkeypatch.delitem(sys.modules, "tavily", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "tavily":
            raise ImportError("No module named 'tavily'")
        return original_import(name, *args, **kwargs)

    import importlib
    import tools.tavily_client as tc

    with patch("builtins.__import__", side_effect=fake_import):
        importlib.reload(tc)
        results = await tc.search("test query")

    assert results == []


@pytest.mark.asyncio
async def test_search_api_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    mock_client_instance = AsyncMock()
    mock_client_instance.search = AsyncMock(side_effect=RuntimeError("API rate limit"))
    mock_async_class = MagicMock(return_value=mock_client_instance)

    fake_tavily = MagicMock()
    fake_tavily.AsyncTavilyClient = mock_async_class
    monkeypatch.setitem(sys.modules, "tavily", fake_tavily)

    import importlib
    import tools.tavily_client as tc
    importlib.reload(tc)

    results = await tc.search("test query")
    assert results == []

    monkeypatch.delitem(sys.modules, "tavily", raising=False)


# ---------------------------------------------------------------------------
# research_business()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_business_success(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    call_count = 0

    async def fake_search(query, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Main business search — URL contains "joespizza" which
            # matches business_name "joespizza" after lowering
            return [
                {
                    "title": "JoesPizza - Home",
                    "url": "https://joespizza.com",
                    "content": "JoesPizza has 4.8 stars rating on Google reviews.",
                    "score": 0.9,
                    "raw_content": "",
                },
                {
                    "title": "JoesPizza on Facebook",
                    "url": "https://facebook.com/joespizza",
                    "content": "Community page for JoesPizza.",
                    "score": 0.7,
                    "raw_content": "",
                },
            ]
        else:
            # Competitor search
            return [
                {
                    "title": "Mario's Pizzeria",
                    "url": "https://mariospizzeria.com",
                    "content": "Best pizza in Brooklyn.",
                    "score": 0.6,
                    "raw_content": "",
                },
            ]

    with patch("tools.tavily_client.search", side_effect=fake_search):
        from tools.tavily_client import research_business

        # Use a name that appears verbatim in the URL
        result = await research_business("JoesPizza", location="Brooklyn, NY")

    assert result["business_name"] == "JoesPizza"
    assert result["has_website"] is True
    assert "https://facebook.com/joespizza" in result["social_media"]
    assert len(result["competitors"]) == 1
    assert result["competitors"][0]["name"] == "Mario's Pizzeria"
    assert "stars" in result["reviews_summary"].lower() or "rating" in result["reviews_summary"].lower()
    assert result["overall_summary"]  # non-empty


@pytest.mark.asyncio
async def test_research_business_no_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    with patch("tools.tavily_client.search", new_callable=AsyncMock, return_value=[]):
        from tools.tavily_client import research_business

        result = await research_business("Nonexistent Business XYZ")

    assert result["business_name"] == "Nonexistent Business XYZ"
    assert result["has_website"] is False
    assert result["competitors"] == []
    assert result["social_media"] == []
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_research_detects_website(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    async def fake_search(query, **kwargs):
        if "alternatives" in query:
            return []
        return [
            {
                "title": "SunriseDental - About",
                "url": "https://sunrisedental.com/about",
                "content": "SunriseDental provides family dentistry.",
                "score": 0.95,
                "raw_content": "",
            },
        ]

    with patch("tools.tavily_client.search", side_effect=fake_search):
        from tools.tavily_client import research_business

        # Use name that matches the URL (lowercased)
        result = await research_business("SunriseDental")

    assert result["has_website"] is True
    assert "family dentistry" in result["website_info"].lower()


@pytest.mark.asyncio
async def test_research_finds_competitors(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-123")

    call_count = 0

    async def fake_search(query, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [
                {
                    "title": "Alpha Cleaning",
                    "url": "https://alphacleaning.com",
                    "content": "Professional cleaning services.",
                    "score": 0.9,
                    "raw_content": "",
                },
            ]
        else:
            return [
                {
                    "title": "Alpha Cleaning",
                    "url": "https://alphacleaning.com",
                    "content": "We are the best.",
                    "score": 0.8,
                    "raw_content": "",
                },
                {
                    "title": "Beta Cleaners",
                    "url": "https://betacleaners.com",
                    "content": "Alternative cleaning service.",
                    "score": 0.7,
                    "raw_content": "",
                },
                {
                    "title": "Gamma Maids",
                    "url": "https://gammamaids.com",
                    "content": "Eco-friendly cleaning.",
                    "score": 0.6,
                    "raw_content": "",
                },
            ]

    with patch("tools.tavily_client.search", side_effect=fake_search):
        from tools.tavily_client import research_business

        result = await research_business("Alpha Cleaning", location="Austin, TX")

    # Alpha Cleaning should be excluded from competitors (same business)
    competitor_names = [c["name"] for c in result["competitors"]]
    assert "Alpha Cleaning" not in competitor_names
    assert "Beta Cleaners" in competitor_names
    assert "Gamma Maids" in competitor_names
    assert len(result["competitors"]) == 2
