"""External source ingestors for DeerFlow research.

These ingestors favor official or stable upstreams:
- arXiv metadata API for fresh papers
- GitHub REST search + trending page for repositories
- Stanford AI/HAI web sources for Stanford-adjacent updates
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

import httpx


UTC = timezone.utc
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_TRENDING_URL = "https://github.com/trending?since=daily"
ARXIV_API_URL = "http://export.arxiv.org/api/query"
STANFORD_HAI_NEWS_URL = "https://hai.stanford.edu/news"
STANFORD_SAIL_BLOG_URL = "https://ai.stanford.edu/blog/"


@dataclass(slots=True)
class ResearchSourceItem:
    source: str
    title: str
    url: str
    published_at: str
    summary: str
    tags: list[str]
    score_hint: str = "fresh"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
            "summary": self.summary,
            "tags": list(self.tags),
            "score_hint": self.score_hint,
        }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _coerce_iso(value: str | None, fallback: datetime | None = None) -> str:
    if value:
        text = value.strip()
        if text:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
            except ValueError:
                pass
    return (fallback or _utc_now()).isoformat()


def _clean_text(value: str, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", unescape(value or "")).strip()
    return cleaned[:limit]


def _research_keywords(topic: str) -> list[str]:
    lowered = topic.lower()
    seeds = {
        "agent", "agents", "automation", "reasoning", "memory", "mcp", "browser",
        "research", "local", "inference", "orchestration", "tool", "workflow",
    }
    extracted = {token for token in re.findall(r"[a-z0-9\-\+]{4,}", lowered) if token not in {"perseus", "objective", "hertz"}}
    ranked = list(dict.fromkeys([*sorted(seeds), *sorted(extracted)]))
    return ranked[:8]


def build_arxiv_query(topic: str) -> str:
    keywords = _research_keywords(topic)
    all_terms = "+OR+".join(f"all:{term}" for term in keywords[:4]) if keywords else "all:agents"
    cats = "+OR+".join(
        [
            "cat:cs.AI",
            "cat:cs.CL",
            "cat:cs.LG",
            "cat:cs.SE",
        ]
    )
    return f"({cats})+AND+({all_terms})"


def parse_arxiv_feed(xml_text: str, *, source: str = "arxiv") -> list[ResearchSourceItem]:
    root = ET.fromstring(xml_text)
    items: list[ResearchSourceItem] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS), limit=180)
        summary = _clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS), limit=420)
        url = entry.findtext("atom:id", default="", namespaces=ATOM_NS).strip()
        published_at = _coerce_iso(entry.findtext("atom:published", default="", namespaces=ATOM_NS))
        tags = [node.attrib.get("term", "") for node in entry.findall("atom:category", ATOM_NS)]
        items.append(
            ResearchSourceItem(
                source=source,
                title=title,
                url=url,
                published_at=published_at,
                summary=summary,
                tags=[tag for tag in tags if tag][:6],
            )
        )
    return items


def parse_github_repo_response(payload: dict[str, Any], *, source: str = "github") -> list[ResearchSourceItem]:
    items: list[ResearchSourceItem] = []
    for repo in payload.get("items", []) or []:
        name = str(repo.get("full_name") or repo.get("name") or "").strip()
        if not name:
            continue
        summary = _clean_text(str(repo.get("description") or ""), limit=320)
        tags = []
        language = str(repo.get("language") or "").strip()
        if language:
            tags.append(language)
        tags.extend(
            part
            for part in (
                f"stars:{repo.get('stargazers_count', 0)}",
                f"forks:{repo.get('forks_count', 0)}",
            )
            if part
        )
        items.append(
            ResearchSourceItem(
                source=source,
                title=name,
                url=str(repo.get("html_url") or "").strip(),
                published_at=_coerce_iso(str(repo.get("created_at") or repo.get("updated_at") or "")),
                summary=summary,
                tags=tags[:6],
                score_hint="fresh+popular",
            )
        )
    return items


def parse_github_trending_html(html: str, *, source: str = "github_trending") -> list[ResearchSourceItem]:
    pattern = re.compile(
        r'<h2[^>]*>\s*<a[^>]*href="(?P<href>/[^"#? ]+/[^"#? ]+)"[^>]*>(?P<name>.*?)</a>.*?'
        r'<p[^>]*>(?P<desc>.*?)</p>.*?'
        r'(?:(?:aria-label="star"|href="[^"]*/stargazers")[^>]*>\s*(?P<stars>[\d,]+))?',
        re.S,
    )
    items: list[ResearchSourceItem] = []
    for match in pattern.finditer(html):
        raw_name = re.sub(r"<[^>]+>", " ", match.group("name"))
        title = _clean_text(raw_name.replace("\n", " ").replace("/", " / "), limit=160)
        href = match.group("href").strip()
        desc = _clean_text(re.sub(r"<[^>]+>", " ", match.group("desc") or ""), limit=240)
        stars = _clean_text(match.group("stars") or "", limit=32)
        items.append(
            ResearchSourceItem(
                source=source,
                title=title,
                url=f"https://github.com{href}",
                published_at=_utc_now().isoformat(),
                summary=desc,
                tags=[tag for tag in [f"stars:{stars}" if stars else "", "trending"] if tag],
                score_hint="trending",
            )
        )
        if len(items) >= 12:
            break
    return items


def parse_stanford_listing_html(html: str, *, source: str) -> list[ResearchSourceItem]:
    items: list[ResearchSourceItem] = []
    link_pattern = re.compile(r'<a[^>]+href="(?P<href>https?://[^"]+|/[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S)
    seen: set[str] = set()
    for match in link_pattern.finditer(html):
        raw_title = re.sub(r"<[^>]+>", " ", match.group("title"))
        title = _clean_text(raw_title, limit=180)
        href = match.group("href").strip()
        if not title or len(title) < 25:
            continue
        if any(skip in href for skip in ("/news", "/tag/", "/author/", "/category/")) and title.lower() in {"news", "events"}:
            continue
        if href.startswith("/"):
            base = "https://hai.stanford.edu" if source == "stanford_hai" else "https://ai.stanford.edu"
            href = f"{base}{href}"
        if href in seen:
            continue
        seen.add(href)
        items.append(
            ResearchSourceItem(
                source=source,
                title=title,
                url=href,
                published_at=_utc_now().isoformat(),
                summary=f"Fresh Stanford source candidate from {source}.",
                tags=["stanford"],
                score_hint="fresh",
            )
        )
        if len(items) >= 12:
            break
    return items


class ResearchSourceFetcher:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds
        self._github_token = os.getenv("GITHUB_TOKEN", "").strip()
        self._github_api_version = os.getenv("GITHUB_API_VERSION", "2022-11-28").strip()

    async def fetch(self, source: str, *, topic: str, limit: int = 12) -> list[ResearchSourceItem]:
        if source == "arxiv":
            return await self._fetch_arxiv(topic=topic, limit=limit)
        if source == "github":
            return await self._fetch_github(topic=topic, limit=limit)
        if source == "stanford":
            return await self._fetch_stanford(topic=topic, limit=limit)
        return []

    async def _fetch_arxiv(self, *, topic: str, limit: int) -> list[ResearchSourceItem]:
        params = {
            "search_query": build_arxiv_query(topic),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": "0",
            "max_results": str(limit),
        }
        text = await self._get_text(ARXIV_API_URL, params=params, headers={"User-Agent": "objective-hertz-deerflow"})
        return parse_arxiv_feed(text, source="arxiv")

    async def _fetch_github(self, *, topic: str, limit: int) -> list[ResearchSourceItem]:
        since = (_utc_now() - timedelta(days=7)).date().isoformat()
        keywords = _research_keywords(topic)
        q = " ".join(keywords[:4]) if keywords else "agents automation"
        params = {
            "q": f"{q} created:>={since}",
            "sort": "updated",
            "order": "desc",
            "per_page": str(limit),
        }
        headers = self._github_headers()
        payload = await self._get_json(f"{GITHUB_API_ROOT}/search/repositories", params=params, headers=headers)
        items = parse_github_repo_response(payload, source="github_search")
        try:
            trending_html = await self._get_text(GITHUB_TRENDING_URL, headers={"User-Agent": headers["User-Agent"]})
            items.extend(parse_github_trending_html(trending_html))
        except Exception:
            pass
        return items[:limit]

    async def _fetch_stanford(self, *, topic: str, limit: int) -> list[ResearchSourceItem]:
        headers = {"User-Agent": "objective-hertz-deerflow"}
        items: list[ResearchSourceItem] = []
        try:
            hai_html = await self._get_text(STANFORD_HAI_NEWS_URL, headers=headers)
            items.extend(parse_stanford_listing_html(hai_html, source="stanford_hai"))
        except Exception:
            pass
        try:
            sail_html = await self._get_text(STANFORD_SAIL_BLOG_URL, headers=headers)
            items.extend(parse_stanford_listing_html(sail_html, source="stanford_sail"))
        except Exception:
            pass
        keywords = set(_research_keywords(topic))
        ranked = sorted(
            items,
            key=lambda item: (
                0 if any(keyword in item.title.lower() or keyword in item.summary.lower() for keyword in keywords) else 1,
                item.title,
            ),
        )
        return ranked[:limit]

    async def _get_text(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> str:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.text

    async def _get_json(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            return {"items": payload}

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._github_api_version,
            "User-Agent": "objective-hertz-deerflow",
        }
        if self._github_token:
            headers["Authorization"] = f"Bearer {self._github_token}"
        return headers

