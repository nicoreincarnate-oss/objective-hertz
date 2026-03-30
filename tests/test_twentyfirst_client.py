"""Tests for 21st.dev REST API client."""

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def load_module():
    return importlib.import_module("tools.twentyfirst_client")


# --- Test 1: success returns dict with text and search_query ---


@pytest.mark.asyncio
async def test_fetch_component_inspiration_returns_text_and_search_query():
    mod = load_module()

    mock_response = MagicMock()
    mock_response.json.return_value = {"text": "function Hero() { return <div>...</div> }"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict("os.environ", {"TWENTYFIRST_API_KEY": "test-key-123"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mod.fetch_component_inspiration(
                message="hero section for dental website",
                search_query="hero split cta",
            )

    assert "text" in result
    assert "search_query" in result
    assert result["search_query"] == "hero split cta"
    assert result["text"] != ""


# --- Test 2: missing API key returns empty text ---


@pytest.mark.asyncio
async def test_fetch_component_inspiration_empty_when_no_api_key():
    mod = load_module()

    with patch.dict("os.environ", {}, clear=True):
        # Ensure TWENTYFIRST_API_KEY is not set
        import os
        os.environ.pop("TWENTYFIRST_API_KEY", None)

        result = await mod.fetch_component_inspiration(
            message="hero section",
            search_query="hero",
        )

    assert result["text"] == ""
    assert result["search_query"] == "hero"
    assert "reason" in result or "error" in result or result["text"] == ""


# --- Test 3: HTTP error returns empty text (non-fatal) ---


@pytest.mark.asyncio
async def test_fetch_component_inspiration_empty_on_http_error():
    mod = load_module()

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = Exception("403 Forbidden")

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict("os.environ", {"TWENTYFIRST_API_KEY": "test-key-123"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mod.fetch_component_inspiration(
                message="hero section",
                search_query="hero",
            )

    assert result["text"] == ""
    assert result["search_query"] == "hero"


# --- Test 4: timeout returns empty text (non-fatal) ---


@pytest.mark.asyncio
async def test_fetch_component_inspiration_empty_on_timeout():
    mod = load_module()

    mock_client = AsyncMock()
    mock_client.post.side_effect = TimeoutError("Connection timed out")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict("os.environ", {"TWENTYFIRST_API_KEY": "test-key-123"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mod.fetch_component_inspiration(
                message="hero section",
                search_query="hero",
            )

    assert result["text"] == ""
    assert result["search_query"] == "hero"


# --- Test 5: truncates response text to max_chars ---


@pytest.mark.asyncio
async def test_fetch_component_inspiration_truncates_to_max_chars():
    mod = load_module()

    long_text = "A" * 5000
    mock_response = MagicMock()
    mock_response.json.return_value = {"text": long_text}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.dict("os.environ", {"TWENTYFIRST_API_KEY": "test-key-123"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await mod.fetch_component_inspiration(
                message="hero section",
                search_query="hero",
                max_chars=1500,
            )

    assert len(result["text"]) <= 1500


# --- Test 6: get_twentyfirst_status blocked when no key ---


def test_get_twentyfirst_status_blocked_without_key():
    mod = load_module()

    with patch.dict("os.environ", {}, clear=True):
        import os
        os.environ.pop("TWENTYFIRST_API_KEY", None)

        status = mod.get_twentyfirst_status()

    assert status["available"] is False
    assert status["mode"] == "blocked"


# --- Test 7: get_twentyfirst_status live when key set ---


def test_get_twentyfirst_status_live_with_key():
    mod = load_module()

    with patch.dict("os.environ", {"TWENTYFIRST_API_KEY": "test-key-123"}):
        status = mod.get_twentyfirst_status()

    assert status["available"] is True
    assert status["mode"] == "live"
