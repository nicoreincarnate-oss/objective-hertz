"""Always-loaded memory index — compact summary injected into every LLM system prompt.

Task 27-03 (E-02): Every LLM call starts with zero memory awareness.  This module
builds a compact index of top memories (by composite score) and caches it so
``generate()`` can inject it into the system prompt at near-zero cost.

The index is rebuilt every 30 minutes (or on demand via ``refresh_memory_index``).
It tries Neo4j first (MAGMA graph), falling back to Postgres ``titan_learnings``.

Gate: ANATOMY_MEMORY_INDEX feature flag.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger("perseus.memory_index")

_MAX_ENTRIES = 200
_MAX_SIZE_CHARS = 25_000  # ~25KB, ~6K tokens
_REFRESH_INTERVAL = 1800  # 30 minutes

_cached_index: str | None = None
_cached_at: float = 0.0


def _is_stale(timestamp_iso: str) -> bool:
    """Check if a memory timestamp is stale using MAGMA staleness detection."""
    try:
        from shared.magma import check_staleness
        anchor = {"node_id": "index-check", "timestamp": timestamp_iso}
        return check_staleness(anchor) is not None
    except (ImportError, AttributeError, ValueError, TypeError, OSError):  # IGUS-FIX: Narrowed exception type (CWE-755)
        return False


def _format_entry(node_id: str, category: str, summary: str, confidence: float, timestamp_iso: str) -> str:
    """Format one memory into a compact single-line index entry (~125 chars max).

    Appends [STALE] marker when the memory's temporal decay is below the
    staleness threshold (Phase 17 check_staleness + Phase 27 memory index
    integration, E-05 verification).
    """
    # Compute age in days
    age_str = "?"
    try:
        ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        age_days = max(0, (now - ts).days)
        age_str = f"{age_days}d"
    except (ValueError, TypeError, AttributeError):
        pass

    # Truncate summary to keep each line compact
    max_summary = 80
    if len(summary) > max_summary:
        summary = summary[:max_summary - 1] + "\u2026"

    stale_marker = " [STALE]" if _is_stale(timestamp_iso) else ""
    return f"[{node_id}] {category} | {summary} (conf={confidence:.2f}, {age_str} old){stale_marker}"


async def _build_from_neo4j() -> list[str]:
    """Query Neo4j for top MemoryNode entries ordered by recency and confidence."""
    try:
        from shared.config import config
        if not config.memory.magma_enabled:
            return []

        from shared.magma import _get_driver, compute_anchor_confidence, temporal_decay_factor

        driver = _get_driver()
        if not driver:
            return []

        entries: list[str] = []
        with driver.session() as session:
            result = session.run(
                """MATCH (n:MemoryNode)
                   WHERE n.content IS NOT NULL
                   RETURN n.node_id AS node_id,
                          n.category AS category,
                          n.content AS content,
                          n.timestamp AS timestamp
                   ORDER BY n.timestamp DESC
                   LIMIT $limit""",
                limit=_MAX_ENTRIES * 2,  # fetch extra, we'll score and trim
            )
            rows = [dict(r) for r in result]

        # Score each row using MAGMA's composite confidence
        scored = []
        for row in rows:
            anchor = {
                "node_id": row.get("node_id", ""),
                "category": row.get("category", ""),
                "timestamp": row.get("timestamp", ""),
                "source": "graph",
                "importance": 0.5,
                "score": 0.5,  # neutral RRF score for index ranking
            }
            conf = compute_anchor_confidence(anchor)
            scored.append((conf, row))

        # Sort by confidence descending, take top _MAX_ENTRIES
        scored.sort(key=lambda x: x[0], reverse=True)

        total_size = 0
        for conf, row in scored[:_MAX_ENTRIES]:
            entry = _format_entry(
                node_id=row.get("node_id", "?"),
                category=row.get("category", "unknown"),
                summary=row.get("content", "")[:200],
                confidence=conf,
                timestamp_iso=row.get("timestamp", ""),
            )
            if total_size + len(entry) + 1 > _MAX_SIZE_CHARS:
                break
            entries.append(entry)
            total_size += len(entry) + 1  # +1 for newline

        return entries

    except (ImportError, AttributeError, OSError, RuntimeError, ValueError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"Neo4j memory index build failed (will try Postgres): {e}")
        return []


async def _build_from_postgres() -> list[str]:
    """Fallback: query titan_learnings table for recent high-confidence memories."""
    try:
        from shared.db import fetch_all

        rows = await fetch_all(
            "SELECT id, category, insight, confidence, created_at "
            "FROM titan_learnings "
            "ORDER BY confidence DESC, created_at DESC "
            "LIMIT %s",
            (_MAX_ENTRIES,),
        )

        entries: list[str] = []
        total_size = 0
        for row in rows:
            entry = _format_entry(
                node_id=str(row.get("id", "?")),
                category=row.get("category", "unknown"),
                summary=row.get("insight", ""),
                confidence=float(row.get("confidence", 0.5)),
                timestamp_iso=str(row.get("created_at", "")),
            )
            if total_size + len(entry) + 1 > _MAX_SIZE_CHARS:
                break
            entries.append(entry)
            total_size += len(entry) + 1

        return entries

    except (OSError, RuntimeError, ValueError, TypeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"Postgres memory index build failed: {e}")
        return []


async def _build_from_magma_provenance() -> list[str]:
    """Phase 29: Query MAGMA provenance table for unified memory index.

    Sources from the memory_provenance table (populated by the unified
    memory bus) enriched with Neo4j content.  Falls back to the existing
    Neo4j/Postgres paths when provenance data is unavailable.
    """
    try:
        from shared.db import fetch_all
        from shared.magma import compute_anchor_confidence

        rows = await fetch_all(
            "SELECT p.magma_node_id, p.source_daemon, p.source_table, p.ingested_at "
            "FROM memory_provenance p "
            "ORDER BY p.ingested_at DESC "
            "LIMIT %s",
            (_MAX_ENTRIES * 2,),
        )
        if not rows or len(rows) < 5:
            return []  # Not enough provenance data, fall back

        entries: list[str] = []
        total_size = 0
        for row in rows:
            node_id = row.get("magma_node_id", "?")
            category = row.get("source_table", "unknown")
            daemon = row.get("source_daemon", "")
            ts_str = str(row.get("ingested_at", ""))
            anchor = {
                "node_id": node_id,
                "category": category,
                "timestamp": ts_str,
                "source": "provenance",
                "importance": 0.5,
                "score": 0.5,
            }
            conf = compute_anchor_confidence(anchor)
            entry = _format_entry(
                node_id=node_id,
                category=f"{daemon}/{category}",
                summary=f"[from {daemon}]",
                confidence=conf,
                timestamp_iso=ts_str,
            )
            if total_size + len(entry) + 1 > _MAX_SIZE_CHARS:
                break
            entries.append(entry)
            total_size += len(entry) + 1

        return entries

    except (ImportError, OSError, RuntimeError, ValueError, TypeError) as e:
        logger.debug("MAGMA provenance index build failed: %s", e)
        return []


async def build_memory_index() -> str:
    """Generate compact memory index from top memories by composite score.

    Phase 29: tries MAGMA provenance (unified bus) first,
    then Neo4j graph, then Postgres titan_learnings as last resort.
    Returns one memory per line, ~125 chars each:
      "[ID] category | summary (conf=0.XX, Nd old)"
    """
    # Phase 29: try unified provenance first
    entries = await _build_from_magma_provenance()
    if not entries:
        entries = await _build_from_neo4j()
    if not entries:
        entries = await _build_from_postgres()

    if not entries:
        return ""

    return "\n".join(entries)


def get_memory_index() -> str:
    """Get cached memory index.  Returns empty string if not built yet or stale."""
    global _cached_index, _cached_at
    if _cached_index and (time.time() - _cached_at < _REFRESH_INTERVAL):
        return _cached_index
    return ""


async def refresh_memory_index() -> str:
    """Rebuild index and update cache."""
    global _cached_index, _cached_at
    _cached_index = await build_memory_index()
    _cached_at = time.time()
    logger.info(f"Memory index refreshed: {len((_cached_index or '').splitlines())} entries, "
                f"{len(_cached_index or '')} chars")
    return _cached_index


def invalidate_cache() -> None:
    """Force rebuild on next access."""
    global _cached_index, _cached_at
    _cached_index = None
    _cached_at = 0.0
