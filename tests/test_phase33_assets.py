"""Tests for Phase 33 asset generation pipeline."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from clawdbot.asset_generator import (
    generate_generative_background,
    generate_svg_icon,
)
from clawdbot.design_tokens import extract_tokens_from_direction


def test_generate_generative_bg_particles():
    """Verify p5.js particle code is generated."""
    tokens = extract_tokens_from_direction({"name": "dark-cinematic"})
    code = generate_generative_background(tokens, style="particles")
    assert "new p5" in code
    assert "particles" in code
    assert tokens.accent in code


def test_generate_generative_bg_gradient_mesh():
    """Verify gradient mesh sketch is generated."""
    tokens = extract_tokens_from_direction({"name": "playful-animated"})
    code = generate_generative_background(tokens, style="gradient_mesh")
    assert "new p5" in code
    assert "noise" in code
    assert tokens.primary in code
    assert tokens.accent in code


@pytest.mark.asyncio
async def test_generate_svg_icon_no_key():
    """Without API key, should return empty string gracefully."""
    with patch.dict("os.environ", {"RECRAFT_API_KEY": ""}, clear=False):
        result = await generate_svg_icon("test icon prompt")
    assert result == ""


@pytest.mark.asyncio
async def test_generate_svg_icon_with_mock():
    """Mock Recraft API and verify SVG download."""
    mock_gen_response = AsyncMock()
    mock_gen_response.status_code = 200
    mock_gen_response.raise_for_status = lambda: None
    mock_gen_response.json = lambda: {
        "data": [{"url": "https://recraft.example.com/icon.svg"}]
    }

    from unittest.mock import MagicMock

    mock_svg_response = MagicMock()
    mock_svg_response.status_code = 200
    mock_svg_response.raise_for_status = lambda: None
    mock_svg_response.text = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>'

    with patch.dict("os.environ", {"RECRAFT_API_KEY": "test-key-123"}, clear=False):
        with patch("clawdbot.asset_generator.httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_gen_response)
            mock_client.get = AsyncMock(return_value=mock_svg_response)
            mock_client_class.return_value = mock_client

            result = await generate_svg_icon("logo for dentist")

    assert "<svg" in result
    assert "circle" in result


def test_design_tokens_in_generative_bg():
    """All generative backgrounds should use token colors."""
    for name in ["minimal-geometric", "dark-cinematic", "yokoo-psychedelic-maximalism"]:
        tokens = extract_tokens_from_direction({"name": name})
        code = generate_generative_background(tokens, style="particles")
        assert tokens.accent in code, f"Missing accent color for {name}"
