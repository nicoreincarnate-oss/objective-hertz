"""21st.dev REST API client — component inspiration fetching.

Calls the 21st.dev Magic API to fetch real component code snippets
for use as structural inspiration in ClawdBot's site builder pipeline.
Components are React/TSX and should be adapted to HTML+Tailwind, never
copied verbatim.

API: POST https://magic.21st.dev/api/fetch-ui
Auth: x-api-key header with TWENTYFIRST_API_KEY env var
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from tools.runtime_honesty import env_is_configured, truth_payload

logger = logging.getLogger("perseus.tools.twentyfirst")

TWENTYFIRST_API_BASE = "https://magic.21st.dev"


def get_twentyfirst_status() -> dict[str, Any]:
    """Check if 21st.dev API is configured and reachable."""
    if not env_is_configured("TWENTYFIRST_API_KEY"):
        return truth_payload(
            "blocked",
            "twentyfirst_api",
            False,
            summary="TWENTYFIRST_API_KEY not configured. Component inspiration unavailable.",
            provider="21st.dev",
        )
    return truth_payload(
        "live",
        "twentyfirst_api",
        True,
        summary="21st.dev Magic API is configured and ready for component fetching.",
        provider="21st.dev",
    )


def _check_twentyfirst_available() -> bool:
    """Return True if 21st.dev API key is configured."""
    return env_is_configured("TWENTYFIRST_API_KEY")


def _headers() -> dict[str, str]:
    """Build request headers for 21st.dev API."""
    return {
        "Content-Type": "application/json",
        "x-api-key": os.getenv("TWENTYFIRST_API_KEY", ""),
    }


async def fetch_component_inspiration(
    message: str,
    search_query: str,
    *,
    max_chars: int = 1500,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch component code from 21st.dev for section inspiration.

    Args:
        message: Description of what component is needed (e.g. "hero section for dental website")
        search_query: Search terms for component lookup (e.g. "hero split cta")
        max_chars: Maximum characters to return from component text (default 1500)
        timeout: HTTP request timeout in seconds

    Returns:
        Dict with "text" (component code, possibly truncated) and "search_query".
        On failure, "text" is empty string and "error" or "reason" explains why.
    """
    if not _check_twentyfirst_available():
        return {"text": "", "search_query": search_query, "reason": "unavailable"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{TWENTYFIRST_API_BASE}/api/fetch-ui",
                json={"message": message, "searchQuery": search_query},
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get("text", "")
            return {"text": text[:max_chars], "search_query": search_query}
    except Exception as e:
        logger.warning("21st.dev component fetch failed: %s", e)
        return {"text": "", "search_query": search_query, "error": str(e)}
