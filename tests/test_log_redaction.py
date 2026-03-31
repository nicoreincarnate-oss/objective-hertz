"""Tests for log redaction formatter and credential pattern expansion."""

from __future__ import annotations

import logging
import time
from unittest.mock import patch


def test_redacting_formatter_strips_stripe_key():
    """RedactingFormatter strips Stripe secret keys."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Payment with sk_live_abc123def456ghi789jkl012mno",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "sk_live_" not in result
        assert "[REDACTED:stripe_secret]" in result


def test_redacting_formatter_strips_telegram_token():
    """RedactingFormatter strips Telegram bot tokens."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Bot token: 123456789:ABCDefGHIJKlmNOpQRStuvWxYz_1234567890",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "123456789:ABC" not in result
        assert "[REDACTED:telegram_token]" in result


def test_redacting_formatter_strips_db_connection():
    """RedactingFormatter strips Postgres connection strings."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Connecting to postgresql://user:pass@localhost:5432/mydb",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "postgresql://" not in result
        assert "[REDACTED:db_connection]" in result


def test_redacting_formatter_strips_jwt():
    """RedactingFormatter strips JWT tokens."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=f"Auth token: {jwt}",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "eyJhbGci" not in result
        assert "[REDACTED:jwt_token]" in result


def test_redacting_formatter_strips_api_key():
    """RedactingFormatter strips Claude API keys (sk- prefix)."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Using key sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "sk-ant-api03" not in result
        assert "[REDACTED:api_key]" in result


def test_redacting_formatter_passthrough_when_disabled():
    """RedactingFormatter passes through unchanged when flag is OFF."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="Token: sk_live_abc123def456ghi789jkl012mno",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "false"}):
        result = redactor.format(record)
        assert "sk_live_" in result  # NOT redacted


def test_redacting_formatter_latency_under_1ms():
    """Redaction must add <1ms latency per log line."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    # Realistic log message with embedded credential
    msg = f"API call to stripe with key sk_live_{'x' * 30} completed in 200ms"
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=msg, args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        start = time.perf_counter_ns()
        for _ in range(1000):
            redactor.format(record)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000

        avg_ms = elapsed_ms / 1000
        assert avg_ms < 1.0, f"Redaction latency {avg_ms:.3f}ms exceeds 1ms target"


def test_credential_stripper_has_15_plus_patterns():
    """Credential stripper must have 15+ patterns per AEGIS requirement."""
    from openjarvis.security.credential_stripper import _CREDENTIAL_PATTERNS

    assert len(_CREDENTIAL_PATTERNS) >= 15, (
        f"AEGIS requires 15+ credential patterns, found {len(_CREDENTIAL_PATTERNS)}. "
        "Missing patterns: Stripe, Telegram, Netlify, Instantly, DB strings, JWT."
    )


def test_redacting_formatter_strips_netlify_token():
    """RedactingFormatter strips Netlify tokens."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    token = "nfp_" + "a" * 45
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=f"Deploy with token {token}",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "nfp_" not in result
        assert "[REDACTED:netlify_token]" in result


def test_redacting_formatter_strips_aws_key():
    """RedactingFormatter strips AWS access key IDs."""
    from shared.log_redaction import RedactingFormatter

    inner = logging.Formatter("%(message)s")
    redactor = RedactingFormatter(inner)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="AWS key AKIAIOSFODNN7EXAMPLE found",
        args=(), exc_info=None,
    )

    with patch.dict("os.environ", {"LOG_REDACTION_ENABLED": "true"}):
        result = redactor.format(record)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED:aws_key]" in result
