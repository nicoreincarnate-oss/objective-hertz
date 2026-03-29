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
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
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
