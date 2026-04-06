"""Tests for Phase 33 Are.na client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clawdbot.arena_client import ArenaClient


@pytest.fixture
def client():
    return ArenaClient(timeout=5.0)


@pytest.mark.asyncio
async def test_search_channels(client):
    """Mock httpx and verify API call structure."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {
        "channels": [
            {
                "id": 123,
                "title": "Web Design Inspiration",
                "slug": "web-design-inspiration",
                "length": 42,
                "status": "public",
                "user": {"username": "designer"},
            }
        ]
    }

    with patch("clawdbot.arena_client.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        channels = await client.search_channels("dental website", per=5)

    assert len(channels) == 1
    assert channels[0]["title"] == "Web Design Inspiration"
    assert channels[0]["slug"] == "web-design-inspiration"
    assert channels[0]["length"] == 42


@pytest.mark.asyncio
async def test_get_image_urls(client):
    """Mock response and verify URL extraction."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {
        "blocks": [
            {
                "id": 1,
                "title": "Design Ref",
                "class": "Image",
                "source": None,
                "image": {"display": {"url": "https://example.com/img1.jpg"}},
            },
            {
                "id": 2,
                "title": "Another",
                "class": "Image",
                "source": None,
                "image": {"display": {"url": "https://example.com/img2.jpg"}},
            },
            {
                "id": 3,
                "title": "No Image",
                "class": "Text",
                "source": None,
                "image": None,
            },
        ]
    }

    with patch("clawdbot.arena_client.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        urls = await client.get_image_urls("dark portfolio", max_results=5)

    assert len(urls) == 2
    assert "https://example.com/img1.jpg" in urls
    assert "https://example.com/img2.jpg" in urls


@pytest.mark.asyncio
async def test_search_blocks(client):
    """Verify block search parses correctly."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = lambda: None
    mock_response.json = lambda: {
        "blocks": [
            {
                "id": 10,
                "title": "Hero Pattern",
                "class": "Image",
                "source": {"url": "https://source.com/page"},
                "image": {"original": {"url": "https://example.com/hero.png"}},
            }
        ]
    }

    with patch("clawdbot.arena_client.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        blocks = await client.search_blocks("hero section")

    assert len(blocks) == 1
    assert blocks[0]["title"] == "Hero Pattern"
    assert blocks[0]["source_url"] == "https://source.com/page"
    assert blocks[0]["image_url"] == "https://example.com/hero.png"
