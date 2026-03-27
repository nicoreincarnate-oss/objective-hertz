"""
Shared test fixtures for Perseus.

Strategy: mock the DB layer so unit tests run without Postgres.
shared/db.py is the single choke point — every daemon goes through it.
"""

from collections import defaultdict
from unittest.mock import patch

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


# ── Module isolation guard ───────────────────────────────────────
# Many test files do `sys.modules["titan.training"] = fake` at module level.
# This poisons later test files that import the real module. This hook
# captures critical modules before each test file runs and restores them after.

_PROTECTED_MODULES = [
    "titan.training", "titan.state_machine", "titan.memory",
    "titan.compliance", "titan.pipeline.follow_up", "titan.pipeline.email_send",
    "titan.pipeline.close_deal", "titan.pipeline.build_site",
    "shared.db", "shared.config", "shared.llm_client", "shared.comms",
    "shared.pipeline_alerts",
    "hermes.web.app", "hermes.web.presenter", "hermes.alerts",
    "hermes.a2a_server", "hermes.telegram_bot",
    "multipart", "multipart.multipart", "multipart.exceptions",
]

_module_snapshot: dict[str, object] = {}


def pytest_collectstart(collector):
    """Save protected modules before collecting each test file."""
    global _module_snapshot
    import sys as _sys
    _module_snapshot = {k: _sys.modules.get(k) for k in _PROTECTED_MODULES}


def pytest_collectreport(report):
    """Restore protected modules after collecting each test file."""
    import sys as _sys
    for mod_name, orig in _module_snapshot.items():
        if orig is not None:
            _sys.modules[mod_name] = orig
        else:
            _sys.modules.pop(mod_name, None)


# ── Per-module execution isolation ───────────────────────────────
# The collection guard above protects import time. This guard protects
# test execution: save modules before each test module runs, restore after.
# Without this, test files that inject sys.modules at module level or inside
# test functions poison later test files in the same pytest run.

_execution_snapshots: dict[str, dict[str, object]] = {}


def pytest_runtest_setup(item):
    """Save protected modules before first test in each module."""
    import sys as _sys
    mod_name = item.module.__name__
    if mod_name not in _execution_snapshots:
        _execution_snapshots[mod_name] = {k: _sys.modules.get(k) for k in _PROTECTED_MODULES}


def pytest_runtest_teardown(item, nextitem):
    """Restore protected modules when leaving a test module."""
    import sys as _sys
    mod_name = item.module.__name__
    # Restore when next item is from a different module (or there's no next item)
    if nextitem is None or nextitem.module.__name__ != mod_name:
        saved = _execution_snapshots.pop(mod_name, None)
        if saved:
            for k, orig in saved.items():
                if orig is not None:
                    _sys.modules[k] = orig
                else:
                    _sys.modules.pop(k, None)


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
