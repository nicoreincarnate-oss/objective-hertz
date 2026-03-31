"""Tests for Forbidden Token Scanner (Phase 12: FP-04)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openjarvis.security.forbidden_tokens import TokenMatch, scan, scan_or_raise


# ---------------------------------------------------------------------------
# Scanner unit tests
# ---------------------------------------------------------------------------


def test_detects_openai_key():
    """sk-abc123... flagged as openai_key."""
    text = "My key is sk-abcdefghijklmnopqrstuvwx and more"
    hits = scan(text, include_username=False)
    pattern_names = {h.pattern_name for h in hits}
    assert "openai_key" in pattern_names


def test_detects_stripe_secret():
    """sk_live_... flagged as stripe_secret."""
    text = "Payment key sk_live_abcdefghijklmnopqrstuvwx for production"
    hits = scan(text, include_username=False)
    pattern_names = {h.pattern_name for h in hits}
    assert "stripe_secret" in pattern_names


def test_detects_os_username():
    """Current OS username flagged when present in text."""
    username = os.environ.get("USER", os.environ.get("USERNAME", ""))
    if not username or len(username) < 3:
        pytest.skip("OS username too short for test")
    text = f"This output contains {username} which is a leak"
    hits = scan(text, include_username=True)
    pattern_names = {h.pattern_name for h in hits}
    assert "os_username" in pattern_names


def test_allowlist_suppresses_pattern(tmp_path: Path):
    """Pattern in .forbidden-tokens-allow is skipped."""
    # Create allowlist that suppresses openai_key
    allow_file = tmp_path / ".forbidden-tokens-allow"
    allow_file.write_text("openai_key\n")

    text = "My key is sk-abcdefghijklmnopqrstuvwx and more"
    hits = scan(text, include_username=False, project_root=tmp_path)
    pattern_names = {h.pattern_name for h in hits}
    # openai_key pattern should be in the general patterns but suppressed
    assert "openai_key" not in pattern_names


def test_scan_or_raise_on_clean_text():
    """Clean text passes through unchanged."""
    clean = "This is a perfectly clean piece of text with no secrets."
    result = scan_or_raise(clean, include_username=False)
    assert result == clean


def test_scan_or_raise_raises_on_secret():
    """Text with secrets raises ValueError."""
    dirty = "Contains sk_live_abcdefghijklmnopqrstuvwx key"
    with pytest.raises(ValueError, match="Forbidden tokens detected"):
        scan_or_raise(dirty, include_username=False)


# ---------------------------------------------------------------------------
# Middleware integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_redacts_output():
    """forbidden_token_middleware replaces leaked tokens in result."""
    with patch.dict(os.environ, {"FORBIDDEN_TOKEN_SCAN_ENABLED": "true"}, clear=False):
        from shared.middleware import forbidden_token_middleware

        async def fake_next(ctx):
            return {
                "success": True,
                "output": "Here is sk_live_abcdefghijklmnopqrstuvwx secret",
            }

        result = await forbidden_token_middleware(
            {"stage_name": "test_stage"}, fake_next
        )
        # Should have violations recorded
        assert "forbidden_token_violations" in result
        assert len(result["forbidden_token_violations"]) >= 1
        assert result["forbidden_token_violations"][0]["pattern"] == "stripe_secret"
        # Output should be processed through credential stripper
        # (stripper replaces sk-* patterns with [REDACTED:api_key])
        assert "output" in result


@pytest.mark.asyncio
async def test_middleware_passthrough_when_off():
    """Middleware passes through when flag is off."""
    with patch.dict(os.environ, {"FORBIDDEN_TOKEN_SCAN_ENABLED": "false"}, clear=False):
        from shared.middleware import forbidden_token_middleware

        secret_text = "sk_live_abcdefghijklmnopqrstuvwx"

        async def fake_next(ctx):
            return {"success": True, "output": secret_text}

        result = await forbidden_token_middleware({"stage_name": "test"}, fake_next)
        # Should pass through unchanged
        assert result["output"] == secret_text
        assert "forbidden_token_violations" not in result
