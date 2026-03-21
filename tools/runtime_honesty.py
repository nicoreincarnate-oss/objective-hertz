"""
Runtime honesty utilities — explicit status reporting for all external tools.
Every tool wrapper reports whether it's live, blocked, or needs input.
"""

import os
from typing import Any


def env_is_configured(key: str) -> bool:
    """Check if an environment variable is set and non-empty."""
    val = os.getenv(key, "")
    return bool(val and val.strip() and val.strip() != "CHANGE_ME")


def truth_payload(
    mode: str,
    data_source: str,
    available: bool,
    *,
    summary: str = "",
    provider: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Build a standardized status payload for any external tool.

    Args:
        mode: "live", "blocked", "needs_input", "degraded"
        data_source: where the data comes from (e.g. "firecrawl_api", "stripe_api")
        available: whether the tool is usable right now
        summary: human-readable explanation
        provider: tool/service name
        **kwargs: additional fields merged into payload
    """
    payload = {
        "mode": mode,
        "data_source": data_source,
        "available": available,
        "summary": summary,
        "provider": provider,
    }
    payload.update(kwargs)
    return payload
