"""Bounded UUID set for A2A message deduplication.

Feature flag: ANATOMY_TASK_RESILIENCE

When enabled, incoming A2A tasks are checked against this set before
dispatch. Duplicates (retries, restarts) are detected in O(1) time with
bounded memory via an LRU eviction policy.
"""

from __future__ import annotations

import threading
from collections import OrderedDict


class BoundedUUIDSet:
    """Circular buffer for message dedup. O(1) lookup, bounded memory."""

    def __init__(self, capacity: int = 2000):
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, message_id: str) -> bool:
        """Add ID. Returns True if new (not seen before), False if duplicate."""
        with self._lock:
            if message_id in self._seen:
                self._seen.move_to_end(message_id)
                return False  # duplicate
            self._seen[message_id] = None
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)  # evict oldest
            return True  # new

    def __contains__(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._seen

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    @property
    def capacity(self) -> int:
        return self._capacity

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    def seed_from_db(self, recent_ids: list[str]) -> None:
        """Seed the set from recent DB entries on startup.

        Call this at boot to prevent re-processing messages that were
        already handled in a previous process lifetime.
        """
        with self._lock:
            for uid in recent_ids[-self._capacity:]:
                self._seen[uid] = None
