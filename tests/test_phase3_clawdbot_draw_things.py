"""Phase 3 Wave 5 — Clawdbot Draw Things opt-in gate tests.

Verifies that the Draw Things local image gen branch in
clawdbot/asset_generator.generate_hero_image is dead by default and that
when enabled but the service is unreachable it falls through cleanly to
the existing Recraft/fal.ai production path (CARL decision 2026-03-30).
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from clawdbot import asset_generator


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_draw_things_client_imports_cleanly():
    """The shared client must import without Draw Things installed."""
    from shared.imagegen.draw_things_client import DrawThingsClient

    client = DrawThingsClient()
    assert client.api_base.startswith("http://127.0.0.1")


def test_draw_things_disabled_by_default(monkeypatch):
    """With ENABLE_DRAW_THINGS unset, the Draw Things branch must NOT be hit.

    We assert this by ensuring DrawThingsClient is never instantiated even
    if we patch it. The hero image call should fall straight through to
    the fal.ai path, which returns b"" when FAL_KEY is not set.
    """
    monkeypatch.delenv("ENABLE_DRAW_THINGS", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)

    with patch(
        "shared.imagegen.draw_things_client.DrawThingsClient"
    ) as mock_client:
        result = _run(asset_generator.generate_hero_image("test prompt"))

    # Branch never hit → mock never called
    assert mock_client.call_count == 0
    # Fal.ai path returned empty (no key)
    assert result == b""


def test_draw_things_enabled_service_unreachable(monkeypatch):
    """With ENABLE_DRAW_THINGS=true but service unreachable, must fall through.

    health_check() returns False → branch is skipped → Recraft/fal.ai path runs.
    With no FAL_KEY set, fal.ai path returns b"" cleanly (no exception).
    """
    monkeypatch.setenv("ENABLE_DRAW_THINGS", "true")
    monkeypatch.delenv("FAL_KEY", raising=False)

    fake_instance = AsyncMock()
    fake_instance.health_check = AsyncMock(return_value=False)
    fake_instance.generate = AsyncMock(
        side_effect=AssertionError("generate must not be called when health_check is False")
    )

    with patch(
        "shared.imagegen.draw_things_client.DrawThingsClient",
        return_value=fake_instance,
    ):
        result = _run(asset_generator.generate_hero_image("test prompt"))

    fake_instance.health_check.assert_awaited_once()
    fake_instance.generate.assert_not_called()
    assert result == b""


def test_draw_things_enabled_generate_raises_falls_through(monkeypatch):
    """If health passes but generate() raises, must fall through to fal.ai cleanly."""
    monkeypatch.setenv("ENABLE_DRAW_THINGS", "true")
    monkeypatch.delenv("FAL_KEY", raising=False)

    fake_instance = AsyncMock()
    fake_instance.health_check = AsyncMock(return_value=True)
    fake_instance.generate = AsyncMock(side_effect=RuntimeError("DT down"))

    with patch(
        "shared.imagegen.draw_things_client.DrawThingsClient",
        return_value=fake_instance,
    ):
        result = _run(asset_generator.generate_hero_image("test prompt"))

    fake_instance.health_check.assert_awaited_once()
    fake_instance.generate.assert_awaited_once()
    # Cleanly fell through to fal.ai (which returns b"" with no key)
    assert result == b""
