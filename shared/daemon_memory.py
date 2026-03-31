"""DeerFlow Persistent Memory — three-tier daemon memory system.

Feature flag: ENABLE_DEERFLOW_MEMORY env var (true/1 to enable).

Three tiers:
  - WorkingMemory: in-process dict, lost on restart
  - DaemonMemoryStore: Postgres-backed episodic (30-day TTL) + semantic (permanent)
  - MemoryCache: JSON file cache for fast startup (write-through to Postgres)

Memory isolation enforced via DNA profile ``memory_domains`` field.

Implements the ``MemoryStore`` Protocol from ``shared.contracts``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from psycopg.types.json import Jsonb

from shared import db

logger = logging.getLogger("perseus.daemon_memory")

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def _memory_enabled() -> bool:
    return os.environ.get("ENABLE_DEERFLOW_MEMORY", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Tier 1: Working memory (in-process only, NOT Postgres)
# ---------------------------------------------------------------------------


class WorkingMemory:
    """In-process working memory. Lost on restart. NOT in Postgres.

    Uses an ``OrderedDict`` for LRU-like eviction: oldest inserted key is
    evicted first when the store exceeds ``max_items``.
    """

    def __init__(self, max_items: int = 100) -> None:
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._max = max_items

        # Session health tracking (Phase 12: FP-05)
        self.total_tokens: int = 0
        self.elapsed_seconds: float = 0.0
        self.error_count: int = 0
        self.state_transitions: int = 0
        self.context_saturation_pct: float = 0.0
        self._session_start: float = time.time()
        self._max_tokens: int = 2_000_000  # 2M token ceiling
        self._max_elapsed: float = 72 * 3600  # 72 hours in seconds

    def get(self, key: str) -> Any:
        """Retrieve a value, returning ``None`` if absent."""
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        """Store a value, evicting the oldest entry if at capacity."""
        if key in self._store:
            # Move to end (most recent)
            self._store.move_to_end(key)
            self._store[key] = value
            return
        if len(self._store) >= self._max:
            self._store.popitem(last=False)  # evict oldest
        self._store[key] = value

    def delete(self, key: str) -> None:
        """Remove a key if present."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries."""
        self._store.clear()

    def items(self) -> list[tuple[str, Any]]:
        """Return all key-value pairs."""
        return list(self._store.items())

    def __len__(self) -> int:
        return len(self._store)

    # -- Session health tracking (Phase 12: FP-05) ---------------------------

    def record_tokens(self, count: int) -> None:
        """Accumulate token usage."""
        self.total_tokens += count
        self.context_saturation_pct = (self.total_tokens / self._max_tokens) * 100

    def record_error(self) -> None:
        """Increment error counter."""
        self.error_count += 1

    def record_state_transition(self) -> None:
        """Increment state transition counter."""
        self.state_transitions += 1

    def update_elapsed(self) -> None:
        """Update elapsed seconds from session start."""
        self.elapsed_seconds = time.time() - self._session_start

    def needs_reset(self) -> bool:
        """Check if session should auto-reset (2M tokens or 72h)."""
        self.update_elapsed()
        return self.total_tokens >= self._max_tokens or self.elapsed_seconds >= self._max_elapsed

    def reset(self) -> None:
        """Reset session health counters and clear working memory."""
        self.total_tokens = 0
        self.elapsed_seconds = 0.0
        self.error_count = 0
        self.state_transitions = 0
        self.context_saturation_pct = 0.0
        self._session_start = time.time()
        self.clear()

    def health_snapshot(self) -> dict:
        """Return current session health as dict (for DB persistence and API)."""
        self.update_elapsed()
        return {
            "total_tokens": self.total_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "error_count": self.error_count,
            "state_transitions": self.state_transitions,
            "context_saturation_pct": round(self.context_saturation_pct, 2),
            "needs_reset": self.needs_reset(),
            "items_count": len(self),
        }


# ---------------------------------------------------------------------------
# Tier 2 & 3: DaemonMemoryStore — episodic + semantic (Postgres-backed)
# ---------------------------------------------------------------------------

# Row cap per daemon before compression triggers
_DEFAULT_ROW_CAP = 10_000


class DaemonMemoryStore:
    """Persistent daemon memory implementing the ``MemoryStore`` Protocol.

    Episodic memories expire after ``ttl_days`` (default 30).
    Semantic memories are permanent but compressed when the per-daemon row
    count exceeds ``row_cap`` (default 10 000).

    Uses ``shared.db.execute`` / ``shared.db.fetch_all`` for async Postgres
    access and ``psycopg.types.json.Jsonb`` for JSONB columns.
    """

    def __init__(self, row_cap: int = _DEFAULT_ROW_CAP) -> None:
        self._row_cap = row_cap

    # -- MemoryStore Protocol methods ----------------------------------------

    async def load(self, daemon_name: str, memory_type: str) -> list[dict]:
        """Load memories of *memory_type* for *daemon_name*."""
        rows = await db.fetch_all(
            """SELECT key, content, importance, access_count, created_at, updated_at
               FROM daemon_memory
               WHERE daemon_name = %s AND memory_type = %s
               ORDER BY importance DESC, updated_at DESC""",
            (daemon_name, memory_type),
        )
        # Increment access_count (fire-and-forget best-effort)
        if rows:
            try:
                await db.execute(
                    """UPDATE daemon_memory
                       SET access_count = access_count + 1
                       WHERE daemon_name = %s AND memory_type = %s""",
                    (daemon_name, memory_type),
                )
            except Exception:
                pass  # non-critical
        return rows

    async def save(self, daemon_name: str, memory_type: str, entry: dict) -> None:
        """Persist a memory *entry* for *daemon_name*.

        ``entry`` must contain at minimum ``key`` and ``content``.
        Optional: ``importance`` (default 0.5), ``ttl_days`` (default 30 for episodic).
        """
        key = entry["key"]
        content = entry.get("content", {})
        importance = entry.get("importance", 0.5)

        if memory_type == "episodic":
            ttl_days = entry.get("ttl_days", 30)
            await self.save_episodic(daemon_name, key, content, importance, ttl_days)
        elif memory_type == "semantic":
            await self.save_semantic(daemon_name, key, content, importance)
        else:
            raise ValueError(f"Unknown memory_type: {memory_type}")

    async def cleanup(self, daemon_name: str) -> int:
        """Remove stale memories for *daemon_name*; return count removed."""
        return await self.cleanup_expired(daemon_name)

    # -- Episodic memory -----------------------------------------------------

    async def save_episodic(
        self,
        daemon_name: str,
        key: str,
        content: dict,
        importance: float = 0.5,
        ttl_days: int = 30,
    ) -> None:
        """Save episodic memory with expiry."""
        expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
        await db.execute(
            """INSERT INTO daemon_memory
                   (daemon_name, memory_type, key, content, importance, expires_at)
               VALUES (%s, 'episodic', %s, %s, %s, %s)
               ON CONFLICT (daemon_name, memory_type, key) DO UPDATE
               SET content = EXCLUDED.content,
                   importance = EXCLUDED.importance,
                   expires_at = EXCLUDED.expires_at,
                   updated_at = NOW()""",
            (daemon_name, key, Jsonb(content), importance, expires_at),
        )

    async def cleanup_expired(self, daemon_name: str | None = None) -> int:
        """Delete expired episodic memories. Run daily via Perseus scheduler.

        If *daemon_name* is ``None``, cleans up all daemons (global sweep).
        Returns the number of rows deleted.
        """
        if daemon_name:
            rows = await db.fetch_all(
                """DELETE FROM daemon_memory
                   WHERE daemon_name = %s
                     AND expires_at IS NOT NULL
                     AND expires_at < NOW()
                   RETURNING id""",
                (daemon_name,),
            )
        else:
            rows = await db.fetch_all(
                """DELETE FROM daemon_memory
                   WHERE expires_at IS NOT NULL AND expires_at < NOW()
                   RETURNING id""",
            )
        count = len(rows)
        if count:
            logger.info("Cleaned up %d expired memories%s", count,
                        f" for {daemon_name}" if daemon_name else "")
        return count

    # -- Semantic memory -----------------------------------------------------

    async def save_semantic(
        self,
        daemon_name: str,
        key: str,
        content: dict,
        importance: float = 0.7,
    ) -> None:
        """Save semantic memory (permanent, no expiry).

        Triggers compression when per-daemon row count exceeds ``row_cap``.
        """
        count = await self._get_row_count(daemon_name)
        if count >= self._row_cap:
            await self._compress_memories(daemon_name)

        await db.execute(
            """INSERT INTO daemon_memory
                   (daemon_name, memory_type, key, content, importance)
               VALUES (%s, 'semantic', %s, %s, %s)
               ON CONFLICT (daemon_name, memory_type, key) DO UPDATE
               SET content = EXCLUDED.content,
                   importance = EXCLUDED.importance,
                   updated_at = NOW()""",
            (daemon_name, key, Jsonb(content), importance),
        )

    async def _get_row_count(self, daemon_name: str) -> int:
        """Return the total memory row count for a daemon."""
        val = await db.fetch_val(
            "SELECT COUNT(*) FROM daemon_memory WHERE daemon_name = %s",
            (daemon_name,),
        )
        return int(val) if val else 0

    async def _compress_memories(self, daemon_name: str) -> None:
        """When row cap exceeded, merge low-importance memories.

        Merges bottom 20% by importance into consolidated entries.
        Uses MAGMA semantic merge if available, otherwise simple JSON merge.
        """
        # Get bottom 20% by importance
        total = await self._get_row_count(daemon_name)
        bottom_limit = max(total // 5, 10)

        rows = await db.fetch_all(
            """SELECT id, key, content, importance
               FROM daemon_memory
               WHERE daemon_name = %s
               ORDER BY importance ASC, access_count ASC
               LIMIT %s""",
            (daemon_name, bottom_limit),
        )
        if len(rows) < 2:
            return

        # Try MAGMA compression first, fall back to simple merge
        merged_content = await self._merge_contents(rows)
        merged_key = f"compressed_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

        # Delete originals
        ids = [r["id"] for r in rows]
        placeholders = ", ".join(["%s"] * len(ids))
        await db.execute(
            f"DELETE FROM daemon_memory WHERE id IN ({placeholders})",
            tuple(ids),
        )

        # Insert consolidated entry
        avg_importance = sum(r["importance"] for r in rows) / len(rows)
        await db.execute(
            """INSERT INTO daemon_memory
                   (daemon_name, memory_type, key, content, importance)
               VALUES (%s, 'semantic', %s, %s, %s)""",
            (daemon_name, merged_key, Jsonb(merged_content), min(avg_importance + 0.1, 1.0)),
        )
        logger.info("Compressed %d low-importance memories for %s into %s",
                     len(rows), daemon_name, merged_key)

    @staticmethod
    async def _merge_contents(rows: list[dict]) -> dict:
        """Merge memory contents. Uses MAGMA if available, else simple merge."""
        try:
            from shared.magma import semantic_merge
            return await semantic_merge([r["content"] for r in rows])
        except (ImportError, AttributeError, Exception):
            # Simple merge fallback: combine all content dicts
            merged: dict[str, Any] = {
                "_compressed": True,
                "_source_count": len(rows),
                "_compressed_at": datetime.now(UTC).isoformat(),
                "entries": [],
            }
            for row in rows:
                merged["entries"].append({
                    "key": row["key"],
                    "content": row["content"],
                    "importance": float(row["importance"]),
                })
            return merged


# ---------------------------------------------------------------------------
# Tier 2.5: JSON cache for fast startup
# ---------------------------------------------------------------------------


class MemoryCache:
    """JSON file cache for fast daemon startup. Write-through to Postgres.

    Cache files live at ``~/.objective-hertz/memory-cache/{daemon}.json``.
    The startup path reads the local JSON file (< 500ms target) while a
    background task reconciles with Postgres as the authoritative source.
    """

    CACHE_DIR = Path.home() / ".objective-hertz" / "memory-cache"

    def __init__(self, store: DaemonMemoryStore | None = None) -> None:
        self._store = store or DaemonMemoryStore()

    def _cache_path(self, daemon_name: str) -> Path:
        return self.CACHE_DIR / f"{daemon_name}.json"

    async def load_cache(self, daemon_name: str) -> dict[str, Any]:
        """Load cached memories from JSON file. Fast startup path."""
        cache_file = self._cache_path(daemon_name)
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupt cache for %s, ignoring: %s", daemon_name, exc)
        return {}

    async def save_cache(self, daemon_name: str, memories: dict[str, Any]) -> None:
        """Write memories to JSON cache file."""
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = self._cache_path(daemon_name)
        try:
            cache_file.write_text(json.dumps(memories, default=str, indent=2))
        except OSError as exc:
            logger.warning("Failed to write cache for %s: %s", daemon_name, exc)

    async def save_with_cache(
        self,
        daemon_name: str,
        key: str,
        content: dict,
        memory_type: str = "episodic",
        **kwargs: Any,
    ) -> None:
        """Write to Postgres AND update JSON cache (write-through)."""
        await self._store.save(
            daemon_name, memory_type, {"key": key, "content": content, **kwargs}
        )
        # Update cache
        cache = await self.load_cache(daemon_name)
        cache[key] = content
        await self.save_cache(daemon_name, cache)

    async def rebuild_cache(self, daemon_name: str) -> dict[str, Any]:
        """Rebuild the local JSON cache from Postgres (authoritative source)."""
        memories: dict[str, Any] = {}
        for mtype in ("episodic", "semantic"):
            rows = await self._store.load(daemon_name, mtype)
            for row in rows:
                memories[row["key"]] = row["content"]
        await self.save_cache(daemon_name, memories)
        logger.info("Rebuilt cache for %s (%d entries)", daemon_name, len(memories))
        return memories

    async def invalidate(self, daemon_name: str) -> None:
        """Delete the local cache file. Next load falls through to Postgres."""
        cache_file = self._cache_path(daemon_name)
        if cache_file.exists():
            cache_file.unlink()
            logger.info("Cache invalidated for %s", daemon_name)


# ---------------------------------------------------------------------------
# Memory isolation via DNA profiles
# ---------------------------------------------------------------------------

_DNA_DIR = Path(__file__).resolve().parent.parent / "soul" / "dna"
_domain_cache: dict[str, list[str]] = {}


def _load_allowed_domains(daemon_name: str) -> list[str]:
    """Read ``memory_domains`` from the daemon's DNA profile.

    Returns a list of domain prefixes the daemon is allowed to access.
    The daemon's own name is always implicitly allowed.
    Results are cached in-process for performance.
    """
    if daemon_name in _domain_cache:
        return _domain_cache[daemon_name]

    dna_file = _DNA_DIR / f"{daemon_name}.yaml"
    domains: list[str] = [daemon_name]  # always allowed to access own memories

    if dna_file.exists():
        try:
            profile = yaml.safe_load(dna_file.read_text()) or {}
            raw_domains = profile.get("memory_domains", [])
            if isinstance(raw_domains, list):
                domains = list(set(domains + raw_domains))
        except (yaml.YAMLError, OSError) as exc:
            logger.warning("Failed to read DNA for %s: %s", daemon_name, exc)

    _domain_cache[daemon_name] = domains
    return domains


def check_memory_access(requesting_daemon: str, target_daemon: str) -> None:
    """Raise ``PermissionError`` if *requesting_daemon* may not access
    *target_daemon*'s memories.

    Access rules:
    - A daemon can always access its own memories.
    - A daemon can access memories whose daemon_name appears in its
      ``memory_domains`` list from its DNA profile.
    """
    if requesting_daemon == target_daemon:
        return  # always allowed

    allowed = _load_allowed_domains(requesting_daemon)
    if target_daemon not in allowed:
        raise PermissionError(
            f"{requesting_daemon} cannot access {target_daemon} memories. "
            f"Allowed domains: {allowed}"
        )


class IsolatedMemoryStore:
    """Wrapper around ``DaemonMemoryStore`` that enforces per-daemon isolation.

    Every call validates that the requesting daemon is allowed to access
    the target daemon's memories via its DNA profile ``memory_domains``.
    """

    def __init__(self, requesting_daemon: str, store: DaemonMemoryStore | None = None) -> None:
        self._requester = requesting_daemon
        self._store = store or DaemonMemoryStore()

    async def load(self, daemon_name: str, memory_type: str) -> list[dict]:
        check_memory_access(self._requester, daemon_name)
        return await self._store.load(daemon_name, memory_type)

    async def save(self, daemon_name: str, memory_type: str, entry: dict) -> None:
        check_memory_access(self._requester, daemon_name)
        await self._store.save(daemon_name, memory_type, entry)

    async def cleanup(self, daemon_name: str) -> int:
        check_memory_access(self._requester, daemon_name)
        return await self._store.cleanup(daemon_name)
