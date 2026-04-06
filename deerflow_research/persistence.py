"""Persistence and deduplication for DeerFlow research items."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from shared import db

logger = logging.getLogger(__name__)

UTC = timezone.utc


async def store_items(items: list[dict], cycle_id: str) -> dict:
    """
    Store research items, deduplicating by URL.
    Returns: {"total": N, "new": M, "duplicates": D}
    """
    total = len(items)
    new_count = 0
    for item in items:
        url = item.get("url", "")
        if not url:
            continue
        row = await db.fetch_one(
            """INSERT INTO research_items
                   (source, url, title, summary, published_at, tags, score_hint, cycle_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO NOTHING
               RETURNING id""",
            (
                item.get("source", "unknown"),
                url,
                item.get("title", ""),
                item.get("summary"),
                item.get("published_at"),
                Jsonb(item.get("tags", [])),
                item.get("score_hint", "fresh"),
                cycle_id,
            ),
        )
        if row is not None:
            new_count += 1
            # Phase 29: emit memory.changed event
            try:
                from shared.comms import MemoryChangedEvent, publish_memory_event
                await publish_memory_event(MemoryChangedEvent(
                    source_daemon="deerflow",
                    memory_type="research",
                    record_id=row["id"],
                    table_name="research_items",
                    action="insert",
                    visibility="public",
                    summary=item.get("title", url)[:120],
                ))
            except (ImportError, ConnectionError, RuntimeError, OSError):
                pass  # Event emission is best-effort
    duplicates = total - new_count
    logger.info(
        "store_items cycle=%s total=%d new=%d dupes=%d",
        cycle_id, total, new_count, duplicates,
    )
    return {"total": total, "new": new_count, "duplicates": duplicates}


async def get_unseen_items(items: list[dict]) -> list[dict]:
    """Filter out items whose URLs are already in research_items table."""
    if not items:
        return []
    urls = [item.get("url", "") for item in items if item.get("url")]
    if not urls:
        return list(items)
    placeholders = ", ".join(["%s"] * len(urls))
    rows = await db.fetch_all(
        f"SELECT url FROM research_items WHERE url IN ({placeholders})",
        tuple(urls),
    )
    existing_urls = {row["url"] for row in rows}
    return [item for item in items if item.get("url") not in existing_urls]


async def mark_item_scored(url: str, score: float, status: str) -> None:
    """Update adoption score and status for an item."""
    await db.execute(
        """UPDATE research_items
           SET adoption_score = %s, adoption_status = %s
           WHERE url = %s""",
        (score, status, url),
    )


async def mark_item_handed_off(url: str, task_id: str) -> None:
    """Mark an item as handed off to implementation queue."""
    await db.execute(
        """UPDATE research_items
           SET handed_off = TRUE, handoff_task_id = %s
           WHERE url = %s""",
        (task_id, url),
    )


async def get_items_for_scoring(limit: int = 50) -> list[dict]:
    """Get pending items that haven't been scored yet."""
    return await db.fetch_all(
        """SELECT id, source, url, title, summary, published_at,
                  tags, score_hint, cycle_id
           FROM research_items
           WHERE adoption_status = 'pending' AND adoption_score IS NULL
           ORDER BY discovered_at DESC
           LIMIT %s""",
        (limit,),
    )


async def get_items_for_handoff(min_score: float = 0.7) -> list[dict]:
    """Get high-scoring items that haven't been handed off."""
    return await db.fetch_all(
        """SELECT id, source, url, title, summary, published_at,
                  tags, score_hint, adoption_score, adoption_status, cycle_id
           FROM research_items
           WHERE adoption_score >= %s AND handed_off = FALSE
           ORDER BY adoption_score DESC""",
        (min_score,),
    )


async def get_recent_items(hours: int = 24, source: str | None = None) -> list[dict]:
    """Get items discovered in the last N hours."""
    if source:
        return await db.fetch_all(
            """SELECT id, source, url, title, summary, published_at,
                      discovered_at, tags, score_hint, adoption_score, adoption_status
               FROM research_items
               WHERE discovered_at >= NOW() - INTERVAL '1 hour' * %s
                 AND source = %s
               ORDER BY discovered_at DESC""",
            (hours, source),
        )
    return await db.fetch_all(
        """SELECT id, source, url, title, summary, published_at,
                  discovered_at, tags, score_hint, adoption_score, adoption_status
           FROM research_items
           WHERE discovered_at >= NOW() - INTERVAL '1 hour' * %s
           ORDER BY discovered_at DESC""",
        (hours,),
    )


async def store_cycle(cycle: dict) -> None:
    """Record a research cycle."""
    await db.execute(
        """INSERT INTO research_cycles
               (cycle_id, started_at, completed_at, topic, mode,
                sources_scanned, items_found, items_new, items_scored,
                status, artifacts, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (cycle_id) DO UPDATE SET
               completed_at = EXCLUDED.completed_at,
               items_found = EXCLUDED.items_found,
               items_new = EXCLUDED.items_new,
               items_scored = EXCLUDED.items_scored,
               status = EXCLUDED.status,
               artifacts = EXCLUDED.artifacts,
               metadata = EXCLUDED.metadata""",
        (
            cycle.get("cycle_id"),
            cycle.get("started_at"),
            cycle.get("completed_at"),
            cycle.get("topic"),
            cycle.get("mode"),
            Jsonb(cycle.get("sources_scanned", [])),
            cycle.get("items_found", 0),
            cycle.get("items_new", 0),
            cycle.get("items_scored", 0),
            cycle.get("status", "running"),
            Jsonb(cycle.get("artifacts", [])),
            Jsonb(cycle.get("metadata", {})),
        ),
    )


async def store_daily_brief(
    brief_date: str,
    content: str,
    recommendations: list,
    handoffs: int,
) -> None:
    """Store a daily evolution brief."""
    await db.execute(
        """INSERT INTO research_daily_briefs
               (brief_date, content_markdown, items_reviewed, recommendations, handoffs_created)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (brief_date) DO UPDATE SET
               content_markdown = EXCLUDED.content_markdown,
               items_reviewed = EXCLUDED.items_reviewed,
               recommendations = EXCLUDED.recommendations,
               handoffs_created = EXCLUDED.handoffs_created""",
        (
            brief_date,
            content,
            len(recommendations),
            Jsonb(recommendations),
            handoffs,
        ),
    )


async def get_latest_brief() -> dict | None:
    """Get the most recent daily brief."""
    return await db.fetch_one(
        """SELECT id, brief_date, created_at, content_markdown,
                  items_reviewed, recommendations, handoffs_created, metadata
           FROM research_daily_briefs
           ORDER BY brief_date DESC
           LIMIT 1""",
    )
