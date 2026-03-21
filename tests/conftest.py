"""
Shared test fixtures for Perseus.

Strategy: mock the DB layer so unit tests run without Postgres.
shared/db.py is the single choke point — every daemon goes through it.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch
from collections import defaultdict

import pytest


# ── In-memory DB mock ──────────────────────────────────────────────

class FakeDB:
    """In-memory stand-in for shared.db. Tracks all writes for assertions."""

    def __init__(self):
        self.tables = {
            "task_queue": [],
            "events": [],
            "system_config": {},
            "budget_tracking": [],
            "clients": [],
            "deals": [],
            "titan_learnings": [],
        }
        self._id_counters = defaultdict(int)

    def _next_id(self, table: str) -> int:
        self._id_counters[table] += 1
        return self._id_counters[table]

    async def execute(self, query: str, params: tuple = ()) -> None:
        pass  # No-op for writes unless overridden per test

    async def fetch_one(self, query: str, params: tuple = ()):
        return None

    async def fetch_all(self, query: str, params: tuple = ()):
        return []

    async def fetch_val(self, query: str, params: tuple = ()):
        return None

    async def insert_task(
        self,
        task_type: str,
        payload: dict = None,
        priority: int = 5,
        dedupe: bool = True,
    ):
        if dedupe:
            # Check dedupe
            for t in self.tables["task_queue"]:
                if t["task_type"] == task_type and t["status"] in ("pending", "running"):
                    return None
        task_id = self._next_id("task_queue")
        self.tables["task_queue"].append({
            "id": task_id,
            "task_type": task_type,
            "payload": payload or {},
            "status": "pending",
            "priority": priority,
        })
        return task_id

    async def emit_event(self, event_type: str, payload: dict = None):
        event_id = self._next_id("events")
        self.tables["events"].append({
            "id": event_id,
            "event_type": event_type,
            "payload": payload or {},
            "acknowledged": False,
        })
        return event_id

    async def get_config(self, key: str, default=None):
        return self.tables["system_config"].get(key, default)

    async def set_config(self, key: str, value):
        self.tables["system_config"][key] = value

    async def init_pool(self, **kwargs):
        pass

    async def close_pool(self):
        pass


@pytest.fixture
def fake_db():
    """Provide a fresh in-memory DB for each test."""
    return FakeDB()


@pytest.fixture
def patch_db(fake_db):
    """Patch shared.db module with fake_db methods."""
    with patch("shared.db.execute", side_effect=fake_db.execute), \
         patch("shared.db.fetch_one", side_effect=fake_db.fetch_one), \
         patch("shared.db.fetch_all", side_effect=fake_db.fetch_all), \
         patch("shared.db.fetch_val", side_effect=fake_db.fetch_val), \
         patch("shared.db.insert_task", side_effect=fake_db.insert_task), \
         patch("shared.db.emit_event", side_effect=fake_db.emit_event), \
         patch("shared.db.get_config", side_effect=fake_db.get_config), \
         patch("shared.db.set_config", side_effect=fake_db.set_config), \
         patch("shared.db.init_pool", side_effect=fake_db.init_pool), \
         patch("shared.db.close_pool", side_effect=fake_db.close_pool):
        yield fake_db
