"""Lightweight Firecrawl API wrapper with explicit runtime honesty."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import requests

from tools.runtime_honesty import env_is_configured, truth_payload

logger = logging.getLogger(__name__)

DEFAULT_HOSTED_FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v1"
DEFAULT_TIMEOUT_SECONDS = 20
MARKDOWN_EXCERPT_LIMIT = 500


def get_firecrawl_api_key() -> str | None:
    """Return the configured Firecrawl API key, if present."""
    return os.getenv("FIRECRAWL_API_KEY")


def _is_hosted_firecrawl_base_url(base_url: str) -> bool:
    return "api.firecrawl.dev" in base_url


def _normalize_firecrawl_base_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return DEFAULT_HOSTED_FIRECRAWL_BASE_URL
    if normalized.endswith(("/v0", "/v1", "/v2")):
        return normalized
    return f"{normalized}/v1"


def _runtime_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "runtime_mode": payload.get("runtime_mode"),
        "base_url": payload.get("base_url"),
        "transport": payload.get("transport"),
        "auth_required": payload.get("auth_required"),
        "auth_configured": payload.get("auth_configured"),
    }


def _resolve_firecrawl_runtime() -> dict[str, Any]:
    self_host_url = (os.getenv("FIRECRAWL_SELF_HOST_URL") or "").strip()
    configured_base_url = (os.getenv("FIRECRAWL_API_BASE_URL") or "").strip()
    base_url = _normalize_firecrawl_base_url(
        self_host_url or configured_base_url or DEFAULT_HOSTED_FIRECRAWL_BASE_URL
    )
    runtime_mode = "self_host" if self_host_url or not _is_hosted_firecrawl_base_url(base_url) else "hosted"
    parsed = urlparse(base_url)
    transport = parsed.scheme or ("https" if runtime_mode == "hosted" else "http")
    auth_configured = env_is_configured("FIRECRAWL_API_KEY")

    return {
        "runtime_mode": runtime_mode,
        "base_url": base_url,
        "transport": transport,
        "auth_required": runtime_mode == "hosted",
        "auth_configured": auth_configured,
        "status_source": "firecrawl_api" if runtime_mode == "hosted" else "firecrawl_self_host",
    }


def get_firecrawl_status() -> dict[str, Any]:
    """Return whether Firecrawl API mode is available at runtime."""
    runtime = _resolve_firecrawl_runtime()
    if runtime["auth_required"] and not runtime["auth_configured"]:
        blocked_runtime = dict(runtime)
        blocked_runtime["runtime_mode"] = "blocked"
        blocked_runtime["configured_runtime_mode"] = runtime["runtime_mode"]
        return truth_payload(
            "blocked",
            "unconfigured",
            False,
            summary="Firecrawl API mode is disabled because FIRECRAWL_API_KEY is missing.",
            provider="firecrawl",
            **blocked_runtime,
        )

    if runtime["runtime_mode"] == "self_host":
        summary = (
            "Firecrawl self-host mode is configured and will target the repo-owned deployment path."
        )
    else:
        summary = "Firecrawl hosted API mode is configured and ready for on-demand web extraction."

    return truth_payload("live", runtime["status_source"], True, summary=summary, provider="firecrawl", **runtime)


def _headers(status: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = get_firecrawl_api_key()
    if status.get("auth_configured") and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post(
    path: str,
    payload: dict[str, Any],
    *,
    status: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    response = requests.post(
        f"{status['base_url'].rstrip('/')}/{path.lstrip('/')}",
        headers=_headers(status),
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {"value": body}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _trim_text(value: Any, *, limit: int = MARKDOWN_EXCERPT_LIMIT) -> str:
    text = _normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _compact_search_results(results: list[Any], *, limit: int) -> list[dict[str, str]]:
    compact_results: list[dict[str, str]] = []
    for item in results[:limit]:
        if not isinstance(item, dict):
            continue
        compact_results.append(
            {
                "title": _normalize_text(item.get("title")),
                "url": _normalize_text(item.get("url")),
                "description": _normalize_text(
                    item.get("description") or item.get("snippet") or item.get("markdown")
                ),
            }
        )
    return compact_results


def _compact_scrape_content(content: dict[str, Any]) -> dict[str, str]:
    raw_metadata = content.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    title = _normalize_text(metadata.get("title") or content.get("title"))
    description = _normalize_text(metadata.get("description") or content.get("description"))
    markdown_excerpt = _trim_text(content.get("markdown") or content.get("content") or content.get("html"))
    return {
        "title": title,
        "description": description,
        "markdown_excerpt": markdown_excerpt,
    }


def _step_truth(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    return {
        "mode": payload.get("mode"),
        "data_source": payload.get("data_source"),
        "runtime_mode": payload.get("runtime_mode"),
        "summary": payload.get("summary"),
    }


def _summarize_research_truth(step_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    live_steps = [payload for payload in step_payloads if payload.get("mode") == "live"]
    blocked_steps = [payload for payload in step_payloads if payload.get("mode") == "blocked"]
    needs_input_steps = [payload for payload in step_payloads if payload.get("mode") == "needs_input"]

    if live_steps and not blocked_steps and not needs_input_steps:
        live_step = live_steps[0]
        source = str(live_step.get("data_source", "firecrawl_api"))
        return truth_payload(
            "live",
            source,
            True,
            summary="Firecrawl completed the requested lead research.",
            provider="firecrawl",
            **_runtime_fields(live_step),
        )

    if live_steps:
        live_step = live_steps[0]
        partial_source = str(live_step.get("data_source", "firecrawl_api")) + "_partial"
        return truth_payload(
            "live",
            partial_source,
            True,
            summary="Firecrawl completed part of the requested lead research.",
            provider="firecrawl",
            **_runtime_fields(live_step),
        )

    if blocked_steps:
        blocked = blocked_steps[0]
        return truth_payload(
            "blocked",
            str(blocked.get("data_source", "firecrawl_api_error")),
            False,
            summary=str(blocked.get("summary", "Firecrawl lead research is blocked.")),
            provider="firecrawl",
            **_runtime_fields(blocked),
        )

    needs_input = needs_input_steps[0]
    return truth_payload(
        "needs_input",
        str(needs_input.get("data_source", "local_validation")),
        False,
        summary=str(needs_input.get("summary", "Provide lead research inputs.")),
        provider="firecrawl",
        **_runtime_fields(needs_input),
    )


def scrape_url(
    url: str,
    *,
    formats: list[str] | None = None,
    only_main_content: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Scrape a page through Firecrawl's hosted API."""
    target_url = (url or "").strip()
    if not target_url:
        return truth_payload(
            "needs_input",
            "local_validation",
            False,
            summary="Provide a URL before requesting Firecrawl scraping.",
            provider="firecrawl",
        )

    status = get_firecrawl_status()
    if status["mode"] != "live":
        return status

    request_payload = {
        "url": target_url,
        "formats": formats or ["markdown"],
        "onlyMainContent": only_main_content,
    }
    runtime_label = "self-host deployment" if status.get("runtime_mode") == "self_host" else "hosted API"
    error_source = "firecrawl_self_host_error" if status.get("runtime_mode") == "self_host" else "firecrawl_api_error"

    try:
        response = _post("scrape", request_payload, status=status, timeout=timeout)
    except Exception as exc:
        logger.warning("Firecrawl scrape failed for %s: %s", target_url, exc)
        return truth_payload(
            "blocked",
            error_source,
            False,
            summary=f"Firecrawl scrape failed: {exc}",
            provider="firecrawl",
            requested_url=target_url,
            **_runtime_fields(status),
        )

    data = response.get("data")
    if not isinstance(data, dict):
        return truth_payload(
            "blocked",
            error_source,
            False,
            summary="Firecrawl returned an unexpected scrape payload.",
            provider="firecrawl",
            requested_url=target_url,
            raw=response,
            **_runtime_fields(status),
        )

    result = {
        "url": target_url,
        "requested_formats": request_payload["formats"],
        "content": data,
    }
    result.update(
        truth_payload(
            "live",
            str(status.get("data_source", "firecrawl_api")),
            True,
            summary=f"Scraped {target_url} via Firecrawl {runtime_label}.",
            provider="firecrawl",
            requested_url=target_url,
            **_runtime_fields(status),
        )
    )
    return result


def search_web(
    query: str,
    *,
    limit: int = 5,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run a hosted Firecrawl web search."""
    normalized_query = (query or "").strip()
    if not normalized_query:
        return truth_payload(
            "needs_input",
            "local_validation",
            False,
            summary="Provide a query before requesting Firecrawl search.",
            provider="firecrawl",
        )

    status = get_firecrawl_status()
    if status["mode"] != "live":
        return status

    runtime_label = "self-host deployment" if status.get("runtime_mode") == "self_host" else "hosted API"
    error_source = "firecrawl_self_host_error" if status.get("runtime_mode") == "self_host" else "firecrawl_api_error"

    try:
        response = _post("search", {"query": normalized_query, "limit": limit}, status=status, timeout=timeout)
    except Exception as exc:
        logger.warning("Firecrawl search failed for %r: %s", normalized_query, exc)
        return truth_payload(
            "blocked",
            error_source,
            False,
            summary=f"Firecrawl search failed: {exc}",
            provider="firecrawl",
            query=normalized_query,
            **_runtime_fields(status),
        )

    results = response.get("data")
    if not isinstance(results, list):
        return truth_payload(
            "blocked",
            error_source,
            False,
            summary="Firecrawl returned an unexpected search payload.",
            provider="firecrawl",
            query=normalized_query,
            raw=response,
            **_runtime_fields(status),
        )

    payload: dict[str, Any] = {
        "query": normalized_query,
        "limit": limit,
        "results": results,
    }
    payload.update(
        truth_payload(
            "live",
            str(status.get("data_source", "firecrawl_api")),
            True,
            summary=f"Firecrawl {runtime_label} search returned {len(results)} result(s).",
            provider="firecrawl",
            query=normalized_query,
            **_runtime_fields(status),
        )
    )
    return payload


def enrich_business_profile(
    business_name: str,
    *,
    city: str = "",
    industry: str = "",
    website_url: str = "",
    search_query: str = "",
    search_limit: int = 3,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Combine Firecrawl search and scrape into compact lead research."""
    normalized_business = _normalize_text(business_name)
    normalized_city = _normalize_text(city)
    normalized_industry = _normalize_text(industry)
    normalized_url = _normalize_text(website_url)
    normalized_query = _normalize_text(search_query) or " ".join(
        part for part in [normalized_business, normalized_city, normalized_industry] if part
    )

    if not normalized_url and not normalized_query:
        return truth_payload(
            "needs_input",
            "local_validation",
            False,
            summary="Provide a website URL or enough lead context to run Firecrawl research.",
            provider="firecrawl",
        )

    search_payload = (
        search_web(normalized_query, limit=search_limit, timeout=timeout)
        if normalized_query
        else None
    )
    scrape_payload = scrape_url(normalized_url, timeout=timeout) if normalized_url else None

    step_payloads = [payload for payload in [search_payload, scrape_payload] if payload]
    payload: dict[str, Any] = {
        "business_name": normalized_business,
        "query": normalized_query,
        "website_url": normalized_url,
        "search_results": [],
        "website_extract": {},
        "signals": {
            "search_attempted": bool(normalized_query),
            "scrape_attempted": bool(normalized_url),
            "search_result_count": 0,
            "website_scraped": False,
        },
        "steps": {
            "search": _step_truth(search_payload),
            "scrape": _step_truth(scrape_payload),
        },
    }

    signals: dict[str, Any] = payload["signals"] if isinstance(payload.get("signals"), dict) else {}

    if search_payload and search_payload.get("mode") == "live":
        compact_results = _compact_search_results(search_payload.get("results", []), limit=search_limit)
        payload["search_results"] = compact_results
        signals["search_result_count"] = len(compact_results)

    if scrape_payload and scrape_payload.get("mode") == "live":
        payload["website_extract"] = _compact_scrape_content(scrape_payload.get("content", {}))
        signals["website_scraped"] = True

    payload["signals"] = signals

    payload.update(_summarize_research_truth(step_payloads))
    return payload
