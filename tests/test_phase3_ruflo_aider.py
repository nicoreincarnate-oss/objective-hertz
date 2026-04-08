"""Phase 3 Wave 2: Ruflo Aider handler opt-in gate tests.

Verifies the ENABLE_AIDER_LOOPS feature flag and the handler's
input validation. Does NOT exercise the full architect→editor→verifier
loop (covered by shared/aider tests in Phase 2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ruflo.aider_handler import aider_loops_enabled, handle_aider_fix_task


def test_aider_loops_disabled_by_default(monkeypatch):
    """When ENABLE_AIDER_LOOPS is unset, the gate must return False."""
    monkeypatch.delenv("ENABLE_AIDER_LOOPS", raising=False)
    assert aider_loops_enabled() is False


def test_aider_loops_enabled_env_var(monkeypatch):
    """When ENABLE_AIDER_LOOPS=true, the gate must return True."""
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "true")
    assert aider_loops_enabled() is True

    # Case-insensitive
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "TRUE")
    assert aider_loops_enabled() is True

    # Anything else is False
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "1")
    assert aider_loops_enabled() is False
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "yes")
    assert aider_loops_enabled() is False


@pytest.mark.asyncio
async def test_handler_returns_skipped_when_disabled(monkeypatch):
    """Handler must short-circuit cleanly when the flag is OFF."""
    monkeypatch.delenv("ENABLE_AIDER_LOOPS", raising=False)

    result = await handle_aider_fix_task(
        {"failing_test": "x", "error_message": "y"},
        llm_client=None,
        repo_root=Path("/tmp"),
    )
    assert result["status"] == "skipped"
    assert "ENABLE_AIDER_LOOPS" in result["error"]


@pytest.mark.asyncio
async def test_handler_requires_failing_test_and_error(monkeypatch):
    """When the flag is ON but payload is incomplete, return failed."""
    monkeypatch.setenv("ENABLE_AIDER_LOOPS", "true")

    # Missing both
    result = await handle_aider_fix_task(
        {},
        llm_client=None,
        repo_root=Path("/tmp"),
    )
    assert result["status"] == "failed"
    assert "Missing failing_test or error_message" in result["error"]

    # Missing error_message
    result = await handle_aider_fix_task(
        {"failing_test": "test_foo"},
        llm_client=None,
        repo_root=Path("/tmp"),
    )
    assert result["status"] == "failed"

    # Missing failing_test
    result = await handle_aider_fix_task(
        {"error_message": "boom"},
        llm_client=None,
        repo_root=Path("/tmp"),
    )
    assert result["status"] == "failed"
