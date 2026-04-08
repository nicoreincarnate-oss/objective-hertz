"""Phase 3 Wave 4 — Hermes voice loop opt-in gate tests.

Verifies the voice_session module:
  * imports cleanly on a machine with no Parakeet/Kokoro services (LAZY imports)
  * stays disabled by default
  * returns status="skipped" when flag is off
  * returns status="skipped" when services are unreachable
"""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest


def test_voice_session_imports_cleanly_without_services():
    """The module must import even when Parakeet/Kokoro daemons are not running."""
    # Ensure flag is off so nothing tries to connect at import time
    with mock.patch.dict(os.environ, {"ENABLE_VOICE_LOOP": "false"}, clear=False):
        import importlib
        import hermes.jarvis.voice_session as vs
        importlib.reload(vs)
        assert hasattr(vs, "handle_voice_input")
        assert hasattr(vs, "voice_loop_enabled")


def test_voice_loop_disabled_by_default():
    from hermes.jarvis.voice_session import voice_loop_enabled
    # Strip the flag entirely
    env = {k: v for k, v in os.environ.items() if k != "ENABLE_VOICE_LOOP"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert voice_loop_enabled() is False


def test_voice_loop_enabled_when_flag_true():
    from hermes.jarvis.voice_session import voice_loop_enabled
    with mock.patch.dict(os.environ, {"ENABLE_VOICE_LOOP": "true"}, clear=False):
        assert voice_loop_enabled() is True


def test_handler_skipped_when_disabled():
    from hermes.jarvis.voice_session import handle_voice_input
    env = {k: v for k, v in os.environ.items() if k != "ENABLE_VOICE_LOOP"}
    with mock.patch.dict(os.environ, env, clear=True):
        result = asyncio.run(handle_voice_input(b"\x00\x00", llm_client=None))
    assert result["status"] == "skipped"
    assert "ENABLE_VOICE_LOOP" in result.get("reason", "")


def test_handler_skipped_when_services_unreachable():
    """When the flag is on but Parakeet/Kokoro health checks fail, status=skipped."""
    from hermes.jarvis import voice_session

    async def fake_health_false(self):
        return False

    with mock.patch.dict(os.environ, {"ENABLE_VOICE_LOOP": "true"}, clear=False):
        with mock.patch(
            "shared.voice.parakeet_client.ParakeetClient.health_check",
            new=fake_health_false,
        ), mock.patch(
            "shared.voice.kokoro_client.KokoroClient.health_check",
            new=fake_health_false,
        ):
            result = asyncio.run(
                voice_session.handle_voice_input(b"\x00\x00", llm_client=None)
            )

    assert result["status"] == "skipped"
    assert "not reachable" in result.get("reason", "") or "health check" in result.get("reason", "")


def test_handler_skipped_when_health_check_raises():
    """Network/timeout exceptions during health checks → graceful skip, not crash."""
    from hermes.jarvis import voice_session

    async def fake_health_raises(self):
        raise ConnectionError("daemon offline")

    with mock.patch.dict(os.environ, {"ENABLE_VOICE_LOOP": "true"}, clear=False):
        with mock.patch(
            "shared.voice.parakeet_client.ParakeetClient.health_check",
            new=fake_health_raises,
        ):
            result = asyncio.run(
                voice_session.handle_voice_input(b"\x00\x00", llm_client=None)
            )

    assert result["status"] == "skipped"
