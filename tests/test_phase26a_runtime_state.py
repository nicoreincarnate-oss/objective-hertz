"""
Tests for Phase 26a — RuntimeState centralized config.

Tasks A-07 (two-tier dataclass) and A-08 (on_state_change + refresh loop).
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure shared.db and transitive deps are importable (stub if missing)
# ---------------------------------------------------------------------------
_sdb = types.ModuleType("shared.db")
_sdb.fetch_all = AsyncMock(return_value=[])  # type: ignore[attr-defined]
_sdb.set_config = AsyncMock()  # type: ignore[attr-defined]
_sdb.get_config = AsyncMock(return_value=None)  # type: ignore[attr-defined]
_sdb.get_conn = AsyncMock()  # type: ignore[attr-defined]
_sdb.init_pool = AsyncMock()  # type: ignore[attr-defined]
_sdb.close_pool = AsyncMock()  # type: ignore[attr-defined]
sys.modules["shared.db"] = _sdb

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(  # type: ignore[attr-defined]
    postgres=types.SimpleNamespace(dsn="postgresql://test:test@localhost:5432/test")
)
sys.modules["shared.config"] = _fake_config

_fake_obs = types.ModuleType("shared.observability")
_fake_obs.capture_exception = lambda *a, **kw: None  # type: ignore[attr-defined]
_fake_obs.enrich_payload_with_context = lambda p: p or {}  # type: ignore[attr-defined]
_fake_obs.observe_db_query = lambda *a, **kw: None  # type: ignore[attr-defined]
sys.modules["shared.observability"] = _fake_obs

# Force reimport of runtime_state to pick up our stubs
sys.modules.pop("shared.runtime_state", None)

# ---------------------------------------------------------------------------
# DB mock helper -- always resolves dynamically so that conftest snapshot
# restoration does not invalidate the reference.
# ---------------------------------------------------------------------------

class _DBProxy:
    """Proxy to shared.db that always resolves the current sys.modules entry."""

    def __getattr__(self, name: str) -> Any:
        db = sys.modules.get("shared.db")
        if db is None:
            raise AttributeError(f"shared.db not in sys.modules, cannot access {name}")
        return getattr(db, name)

    def __setattr__(self, name: str, value: Any) -> None:
        db = sys.modules.get("shared.db")
        if db is None:
            return  # silently ignore if module isn't loaded yet
        setattr(db, name, value)


_fake_db = _DBProxy()


@pytest.fixture(autouse=True)
def _ensure_db_mocked():
    """Ensure shared.db functions are AsyncMocks before every test.

    The conftest snapshot/restore may remove shared.db from sys.modules
    between test collection and execution, so we must re-install our stub
    if it's gone.
    """
    # Always re-inject stubs (conftest may have restored real modules)
    _sdb_local = types.ModuleType("shared.db")
    _sdb_local.fetch_all = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    _sdb_local.set_config = AsyncMock()  # type: ignore[attr-defined]
    _sdb_local.get_config = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    _sdb_local.get_conn = AsyncMock()  # type: ignore[attr-defined]
    _sdb_local.init_pool = AsyncMock()  # type: ignore[attr-defined]
    _sdb_local.close_pool = AsyncMock()  # type: ignore[attr-defined]
    sys.modules["shared.db"] = _sdb_local

    _fc = types.ModuleType("shared.config")
    _fc.config = types.SimpleNamespace(  # type: ignore[attr-defined]
        postgres=types.SimpleNamespace(dsn="postgresql://test:test@localhost:5432/test")
    )
    sys.modules["shared.config"] = _fc

    _fo = types.ModuleType("shared.observability")
    _fo.capture_exception = lambda *a, **kw: None  # type: ignore[attr-defined]
    _fo.enrich_payload_with_context = lambda p: p or {}  # type: ignore[attr-defined]
    _fo.observe_db_query = lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["shared.observability"] = _fo

    db = sys.modules["shared.db"]
    originals = {}
    for attr_name in ("fetch_all", "set_config", "get_config"):
        orig = getattr(db, attr_name, None)
        if not isinstance(orig, AsyncMock):
            originals[attr_name] = orig
            setattr(db, attr_name, AsyncMock(return_value=[] if attr_name == "fetch_all" else None))
    yield
    for attr_name, orig in originals.items():
        setattr(db, attr_name, orig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state():
    """Create a fresh RuntimeState (avoid singleton bleed between tests)."""
    from shared.runtime_state import RuntimeState
    return RuntimeState()


def _mock_db_rows(overrides: dict | None = None) -> list[dict]:
    """Simulate rows from ``SELECT key, value FROM system_config``."""
    base = {
        "review_mode": False,
        "monthly_budget_usd": 500.0,
        "titan_paused": True,
        "model_genius": "claude-opus-4",
        "email_daily_target": 2000,
        "expansion_enabled": False,
        "infra_health": {"pg": "ok", "redis": "ok"},
    }
    if overrides:
        base.update(overrides)
    return [{"key": k, "value": v} for k, v in base.items()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultValues:
    """A-07: fresh state has sensible defaults."""

    def test_defaults(self):
        state = _make_state()
        assert state.review_mode is True
        assert state.monthly_budget_usd == 800.0
        assert state.titan_paused is False
        assert state.pipeline_enabled is True
        assert state.model_fast == "claude-haiku-4-20250414"
        assert state.warm_up_phase is True
        assert state.expansion_enabled is True
        assert state.extra == {}
        assert state.feature_flags == {}
        assert isinstance(state.last_refreshed, datetime)
        assert state.refresh_count == 0


class TestLoadFromDB:
    """A-07: mock DB returns config, state populated correctly."""

    @pytest.mark.asyncio
    async def test_load_populates_mapped_fields(self):
        state = _make_state()
        rows = _mock_db_rows()

        _fake_db.fetch_all = AsyncMock(return_value=rows)
        await state.load_from_db()

        assert state.review_mode is False
        assert state.monthly_budget_usd == 500.0
        assert state.titan_paused is True
        assert state.model_genius == "claude-opus-4"
        assert state.email_daily_target == 2000
        assert state.expansion_enabled is False
        assert state.infra_health == {"pg": "ok", "redis": "ok"}
        assert state.refresh_count == 1

    @pytest.mark.asyncio
    async def test_load_puts_unknown_keys_in_extra(self):
        state = _make_state()
        rows = [{"key": "custom_widget_mode", "value": "turbo"}]

        _fake_db.fetch_all = AsyncMock(return_value=rows)
        await state.load_from_db()

        assert state.extra["custom_widget_mode"] == "turbo"


class TestSetUpdatesMemory:
    """A-07: set() changes in-memory value immediately."""

    @pytest.mark.asyncio
    async def test_set_mapped_key(self):
        state = _make_state()
        assert state.review_mode is True

        _fake_db.set_config = AsyncMock()
        await state.set("review_mode", False)

        assert state.review_mode is False
        _fake_db.set_config.assert_awaited_once_with("review_mode", False)

    @pytest.mark.asyncio
    async def test_set_unmapped_key(self):
        state = _make_state()

        _fake_db.set_config = AsyncMock()
        await state.set("my_custom_key", 42)

        assert state.extra["my_custom_key"] == 42

    @pytest.mark.asyncio
    async def test_set_no_persist(self):
        state = _make_state()

        _fake_db.set_config = AsyncMock()
        await state.set("titan_paused", True, persist=False)

        assert state.titan_paused is True
        _fake_db.set_config.assert_not_awaited()


class TestGetReturnsCurrent:
    """A-07: get() reads from memory (instant, no DB)."""

    def test_get_mapped(self):
        state = _make_state()
        state.monthly_budget_usd = 999.0
        assert state.get("monthly_budget_usd") == 999.0

    def test_get_extra(self):
        state = _make_state()
        state.extra["foo"] = "bar"
        assert state.get("foo") == "bar"

    def test_get_default(self):
        state = _make_state()
        assert state.get("nonexistent_key", "fallback") == "fallback"

    def test_get_default_none(self):
        state = _make_state()
        assert state.get("nonexistent_key") is None


class TestChangeCallbackFires:
    """A-08: callback called when state changes via set()."""

    @pytest.mark.asyncio
    async def test_sync_callback(self):
        state = _make_state()
        received: list[dict] = []

        def on_change(diff):
            received.append(diff)

        state.on_state_change(on_change)

        _fake_db.set_config = AsyncMock()
        await state.set("titan_paused", True)

        assert len(received) == 1
        assert "titan_paused" in received[0]
        old, new = received[0]["titan_paused"]
        assert old is False
        assert new is True

    @pytest.mark.asyncio
    async def test_async_callback(self):
        state = _make_state()
        received: list[dict] = []

        async def on_change(diff):
            received.append(diff)

        state.on_state_change(on_change)

        _fake_db.set_config = AsyncMock()
        await state.set("monthly_budget_usd", 600.0)

        assert len(received) == 1
        assert "monthly_budget_usd" in received[0]

    @pytest.mark.asyncio
    async def test_no_callback_when_value_unchanged(self):
        state = _make_state()
        received: list[dict] = []

        def on_change(diff):
            received.append(diff)

        state.on_state_change(on_change)

        _fake_db.set_config = AsyncMock()
        # Set to current default — no change
        await state.set("review_mode", True)

        assert len(received) == 0


class TestDiffDetectsChanges:
    """A-08: _diff only includes changed fields."""

    def test_no_changes(self):
        from shared.runtime_state import RuntimeState
        old = {"a": 1, "b": "x"}
        new = {"a": 1, "b": "x"}
        assert RuntimeState._diff(old, new) == {}

    def test_value_changed(self):
        from shared.runtime_state import RuntimeState
        old = {"a": 1, "b": "x"}
        new = {"a": 2, "b": "x"}
        diff = RuntimeState._diff(old, new)
        assert diff == {"a": (1, 2)}

    def test_new_key(self):
        from shared.runtime_state import RuntimeState
        old = {"a": 1}
        new = {"a": 1, "b": 2}
        diff = RuntimeState._diff(old, new)
        assert diff == {"b": (None, 2)}

    def test_removed_key(self):
        from shared.runtime_state import RuntimeState
        old = {"a": 1, "b": 2}
        new = {"a": 1}
        diff = RuntimeState._diff(old, new)
        assert diff == {"b": (2, None)}

    def test_dict_field_changed(self):
        from shared.runtime_state import RuntimeState
        old = {"h": {"pg": "ok"}}
        new = {"h": {"pg": "degraded"}}
        diff = RuntimeState._diff(old, new)
        assert "h" in diff


class TestRefreshLoopRuns:
    """A-08: mock timer, verify DB queried periodically and callbacks fire."""

    @pytest.mark.asyncio
    async def test_refresh_loop_queries_db(self):
        state = _make_state()
        call_count = 0

        async def fake_fetch_all(query, params=()):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [{"key": "review_mode", "value": True}]
            else:
                return [{"key": "review_mode", "value": False}]

        callback_diffs: list[dict] = []

        def on_change(diff):
            callback_diffs.append(diff)

        state.on_state_change(on_change)

        _fake_db.fetch_all = fake_fetch_all

        # Load initial state
        await state.load_from_db()
        assert state.review_mode is True

        # Start refresh loop with very short interval
        await state.start_refresh_loop(interval=0.05)

        # Wait for at least one refresh cycle
        await asyncio.sleep(0.15)

        await state.stop_refresh_loop()

        # The loop should have refreshed at least once
        assert call_count >= 2
        # State should reflect the changed value
        assert state.review_mode is False
        # Callback should have fired for the change
        assert len(callback_diffs) >= 1
        assert "review_mode" in callback_diffs[0]

    @pytest.mark.asyncio
    async def test_refresh_loop_survives_error(self):
        """DB errors during refresh should not kill the loop."""
        state = _make_state()
        call_count = 0

        async def flaky_fetch_all(query, params=()):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("DB gone")
            return [{"key": "review_mode", "value": True}]

        _fake_db.fetch_all = flaky_fetch_all

        await state.start_refresh_loop(interval=0.05)
        await asyncio.sleep(0.2)
        await state.stop_refresh_loop()

        # Should have retried after the error
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_duplicate_start_is_noop(self):
        """Starting the refresh loop twice should not create two tasks."""
        state = _make_state()

        _fake_db.fetch_all = AsyncMock(return_value=[])

        await state.start_refresh_loop(interval=60.0)
        task1 = state._refresh_task
        await state.start_refresh_loop(interval=60.0)
        task2 = state._refresh_task
        assert task1 is task2
        await state.stop_refresh_loop()


class TestToDict:
    """Serialization for dashboard / logging."""

    def test_to_dict_includes_mapped_and_extra(self):
        state = _make_state()
        state.extra["custom_key"] = "custom_val"
        d = state.to_dict()
        assert d["review_mode"] is True
        assert d["monthly_budget_usd"] == 800.0
        assert d["extra"]["custom_key"] == "custom_val"
        assert "last_refreshed" in d
        assert "refresh_count" in d


class TestFeatureFlag:
    """Feature flag gating."""

    @pytest.mark.asyncio
    async def test_init_skips_when_flag_disabled(self):
        from shared.runtime_state import init_runtime_state

        _fake_db.fetch_all = AsyncMock(return_value=[])

        with patch.dict(os.environ, {"ANATOMY_RUNTIME_STATE": ""}, clear=False):
            await init_runtime_state()
            _fake_db.fetch_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_init_runs_when_flag_enabled(self):
        from shared.runtime_state import init_runtime_state

        _fake_db.fetch_all = AsyncMock(return_value=[])

        with patch.dict(os.environ, {"ANATOMY_RUNTIME_STATE": "true"}, clear=False):
            state = await init_runtime_state()
            await state.stop_refresh_loop()
            assert state.refresh_count >= 1
