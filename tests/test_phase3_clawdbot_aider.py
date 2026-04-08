"""Phase 3 Wave 3 -- Clawdbot Aider build opt-in gate tests.

These tests verify the feature flag and pre-flight validation in
``clawdbot.aider_build``. They do NOT exercise the canonical
``shared.aider.clawdbot_loop`` runner (covered in Phase 2 tests).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from clawdbot.aider_build import aider_build_enabled, handle_aider_site_build


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    """Ensure ENABLE_AIDER_LOOPS is unset for each test by default."""
    monkeypatch.delenv("ENABLE_AIDER_LOOPS", raising=False)
    yield


def test_aider_build_disabled_by_default():
    """Without the env var, the feature gate must report disabled."""
    assert aider_build_enabled() is False


def test_aider_build_enabled_when_flag_set(monkeypatch):
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "true")
    assert aider_build_enabled() is True


def test_handler_returns_skipped_when_disabled():
    """Handler must short-circuit with status='skipped' when the flag is OFF."""
    site_spec = {"brief": "Test landing page", "brand": {"name": "Acme"}}
    result = asyncio.run(
        handle_aider_site_build(
            site_spec,
            llm_client=None,
            workspace=Path("/tmp/test_workspace"),
        )
    )
    assert result["status"] == "skipped"
    assert "ENABLE_AIDER_LOOPS" in result["reason"]


def test_handler_requires_brief(monkeypatch):
    """When enabled but brief missing, handler must return failed (not crash)."""
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "true")
    site_spec = {"brand": {"name": "Acme"}}  # no brief
    result = asyncio.run(
        handle_aider_site_build(
            site_spec,
            llm_client=None,
            workspace=Path("/tmp/test_workspace"),
        )
    )
    assert result["status"] == "failed"
    assert "brief" in result["error"].lower()
