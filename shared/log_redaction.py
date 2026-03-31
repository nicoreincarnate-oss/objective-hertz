"""Log redaction: strips credentials from all log messages.

Wraps any logging.Formatter. Runs credential_stripper.strip() on every
formatted log line. Must add <1ms latency per log line.

Feature flag: LOG_REDACTION_ENABLED
"""

from __future__ import annotations

import logging
import os

from openjarvis.security.credential_stripper import CredentialStripper

_stripper = CredentialStripper()


def _redaction_enabled() -> bool:
    return os.environ.get("LOG_REDACTION_ENABLED", "").lower() in ("true", "1")


class RedactingFormatter(logging.Formatter):
    """Formatter wrapper that redacts credentials from log output.

    Delegates formatting to an inner formatter, then runs credential
    stripping on the result. Adds <1ms per log line.

    Usage:
        inner = logging.Formatter("%(asctime)s %(message)s")
        redacting = RedactingFormatter(inner)
        handler.setFormatter(redacting)
    """

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        formatted = self._inner.format(record)
        if _redaction_enabled():
            return _stripper.strip(formatted)
        return formatted

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return self._inner.formatTime(record, datefmt)

    def formatException(self, ei) -> str:
        return self._inner.formatException(ei)

    def formatStack(self, stack_info: str) -> str:
        return self._inner.formatStack(stack_info)
