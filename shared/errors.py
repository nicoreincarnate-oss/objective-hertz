"""Error classification for daemon task execution.

Ported from OpenJarvis agents/errors.py. Gives daemons smarter retry logic:
- RetryableError: transient, try again with backoff
- FatalError: permanent, don't retry, alert operator
- EscalateError: uncertain, needs human input

Usage in a daemon's task handler:
    try:
        await do_work()
    except Exception as e:
        classified = classify_error(e)
        if classified.retryable:
            await requeue_with_backoff(task_id, retry_delay(attempt))
        elif classified.needs_human:
            await send_alert(f"Need help: {e}")
        else:
            await fail_task(task_id, str(e))  # dead-letter after 3x
"""

from __future__ import annotations


class DaemonError(Exception):
    """Base class for classified daemon errors."""
    retryable: bool = False
    needs_human: bool = False


class RetryableError(DaemonError):
    """Transient error — retry with backoff."""
    retryable = True


class FatalError(DaemonError):
    """Permanent error — don't retry, alert operator."""
    retryable = False


class EscalateError(DaemonError):
    """Agent is uncertain — needs human input."""
    retryable = False
    needs_human = True


_RETRYABLE_PATTERNS = (
    "rate limit", "rate_limit", "too many requests",
    "timeout", "timed out", "connection reset", "connection refused",
    "temporary", "unavailable", "503", "429", "502",
    "overloaded", "server error",
)

_FATAL_PATTERNS = (
    "permission", "access denied", "unauthorized", "forbidden",
    "invalid api key", "invalid_api_key", "not found",
    "401", "403", "404",
)


def classify_error(exc: Exception) -> DaemonError:
    """Classify an arbitrary exception into Retryable, Fatal, or Escalate."""
    if isinstance(exc, DaemonError):
        return exc

    msg = str(exc).lower()

    if isinstance(exc, PermissionError):
        return FatalError(str(exc))
    for pattern in _FATAL_PATTERNS:
        if pattern in msg:
            return FatalError(str(exc))

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return RetryableError(str(exc))
    for pattern in _RETRYABLE_PATTERNS:
        if pattern in msg:
            return RetryableError(str(exc))

    # Default: retryable (better to retry than give up)
    return RetryableError(str(exc))


def retry_delay(attempt: int) -> int:
    """Exponential backoff: min(10 * 2^attempt, 300) seconds."""
    return min(10 * (2 ** attempt), 300)
