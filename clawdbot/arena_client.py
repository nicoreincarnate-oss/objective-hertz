"""Are.na design reference API client.

Programmatic access to Are.na's public REST API for discovering
design references, color palettes, typography, and layout patterns.
No authentication required for public endpoints.

API docs: https://dev.are.na/documentation
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("perseus.clawdbot.arena_client")

BASE_URL = "https://api.are.na/v2"

_DEFAULT_TIMEOUT = 30.0


class ArenaClient:
    """Async client for the Are.na public API."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def search_channels(self, query: str, per: int = 10) -> list[dict[str, Any]]:
        """Search Are.na channels by topic.

        Args:
            query: Search term (e.g., 'dental website design').
            per: Number of results per page.

        Returns:
            List of channel dicts with id, title, slug, length, etc.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{BASE_URL}/search/channels",
                params={"q": query, "per": per},
            )
            resp.raise_for_status()
            data = resp.json()

        channels = data.get("channels", [])
        if not isinstance(channels, list):
            return []

        return [
            {
                "id": ch.get("id"),
                "title": ch.get("title", ""),
                "slug": ch.get("slug", ""),
                "length": ch.get("length", 0),
                "status": ch.get("status", ""),
                "user": (ch.get("user") or {}).get("username", ""),
            }
            for ch in channels
        ]

    async def get_channel_blocks(
        self, slug: str, per: int = 20
    ) -> list[dict[str, Any]]:
        """Get blocks from a specific channel.

        Args:
            slug: Channel slug (from search results).
            per: Number of blocks per page.

        Returns:
            List of block dicts with id, title, class, image url, etc.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{BASE_URL}/channels/{slug}/contents",
                params={"per": per},
            )
            resp.raise_for_status()
            data = resp.json()

        contents = data.get("contents", [])
        if not isinstance(contents, list):
            return []

        result: list[dict[str, Any]] = []
        for block in contents:
            entry: dict[str, Any] = {
                "id": block.get("id"),
                "title": block.get("title", ""),
                "class": block.get("class", ""),
                "source_url": (block.get("source") or {}).get("url", ""),
            }
            image = block.get("image")
            if isinstance(image, dict):
                entry["image_url"] = (
                    image.get("display", {}).get("url", "")
                    or image.get("original", {}).get("url", "")
                )
            else:
                entry["image_url"] = ""
            result.append(entry)

        return result

    async def search_blocks(self, query: str, per: int = 20) -> list[dict[str, Any]]:
        """Search all blocks by keyword.

        Args:
            query: Search term (e.g., 'minimal hero section').
            per: Number of results per page.

        Returns:
            List of block dicts.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{BASE_URL}/search/blocks",
                params={"q": query, "per": per},
            )
            resp.raise_for_status()
            data = resp.json()

        blocks = data.get("blocks", [])
        if not isinstance(blocks, list):
            return []

        result: list[dict[str, Any]] = []
        for block in blocks:
            entry: dict[str, Any] = {
                "id": block.get("id"),
                "title": block.get("title", ""),
                "class": block.get("class", ""),
                "source_url": (block.get("source") or {}).get("url", ""),
            }
            image = block.get("image")
            if isinstance(image, dict):
                entry["image_url"] = (
                    image.get("display", {}).get("url", "")
                    or image.get("original", {}).get("url", "")
                )
            else:
                entry["image_url"] = ""
            result.append(entry)

        return result

    async def get_image_urls(
        self, query: str, max_results: int = 10
    ) -> list[str]:
        """Search for images matching a mood/style query.

        Searches blocks and extracts image URLs from the results.
        Useful for feeding the taste analyzer with reference images.

        Args:
            query: Design-oriented search (e.g., 'dark minimal portfolio').
            max_results: Maximum image URLs to return.

        Returns:
            List of image URLs.
        """
        blocks = await self.search_blocks(query, per=max_results * 2)

        urls: list[str] = []
        for block in blocks:
            if len(urls) >= max_results:
                break
            image_url = block.get("image_url", "")
            if image_url and image_url not in urls:
                urls.append(image_url)

        return urls
