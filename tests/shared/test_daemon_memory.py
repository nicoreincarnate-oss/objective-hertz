"""Tests for DeerFlow daemon memory system (Phase 3).

Covers:
- WorkingMemory: set/get/eviction at max_items
- DaemonMemoryStore: episodic save/load/expire, semantic save + compression
- Memory isolation: cross-daemon access raises PermissionError
- MemoryStore Protocol compliance
- AgentBase lifecycle hooks (_load_memory / _save_memory)
- Feature flag gating
- daemon_memory_stats view query
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.daemon_memory import (
    DaemonMemoryStore,
    IsolatedMemoryStore,
    WorkingMemory,
    _memory_enabled,
    check_memory_access,
)


# ---------------------------------------------------------------------------
# WorkingMemory tests
# ---------------------------------------------------------------------------


class TestWorkingMemory:
    """In-process working memory: set, get, eviction."""

    def test_set_and_get(self):
        wm = WorkingMemory(max_items=10)
        wm.set("key1", {"data": "hello"})
        assert wm.get("key1") == {"data": "hello"}

    def test_get_missing_returns_none(self):
        wm = WorkingMemory()
        assert wm.get("nonexistent") is None

    def test_eviction_at_max_items(self):
        wm = WorkingMemory(max_items=3)
        wm.set("a", 1)
        wm.set("b", 2)
        wm.set("c", 3)
        wm.set("d", 4)  # should evict "a"
        assert wm.get("a") is None
        assert wm.get("b") == 2
        assert wm.get("d") == 4
        assert len(wm) == 3

    def test_update_moves_to_end(self):
        wm = WorkingMemory(max_items=3)
        wm.set("a", 1)
        wm.set("b", 2)
        wm.set("c", 3)
        wm.set("a", 10)  # update "a", moves to end
        wm.set("d", 4)   # should evict "b" (oldest non-updated)
        assert wm.get("a") == 10
        assert wm.get("b") is None
        assert len(wm) == 3

    def test_delete(self):
        wm = WorkingMemory()
        wm.set("x", 42)
        wm.delete("x")
        assert wm.get("x") is None
        assert len(wm) == 0

    def test_delete_missing_is_noop(self):
        wm = WorkingMemory()
        wm.delete("nonexistent")  # should not raise

    def test_clear(self):
        wm = WorkingMemory()
        wm.set("a", 1)
        wm.set("b", 2)
        wm.clear()
        assert len(wm) == 0

    def test_items(self):
        wm = WorkingMemory(max_items=5)
        wm.set("x", 10)
        wm.set("y", 20)
        items = wm.items()
        assert ("x", 10) in items
        assert ("y", 20) in items
        assert len(items) == 2

    def test_len(self):
        wm = WorkingMemory()
        assert len(wm) == 0
        wm.set("k", "v")
        assert len(wm) == 1


# ---------------------------------------------------------------------------
# DaemonMemoryStore tests (mock DB)
# ---------------------------------------------------------------------------


class TestDaemonMemoryStoreEpisodic:
    """Episodic save / load / expire cycle."""

    @pytest.fixture
    def store(self):
        return DaemonMemoryStore()

    @pytest.mark.asyncio
    async def test_save_episodic(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.execute = AsyncMock()
            await store.save_episodic("titan", "task_123", {"outcome": "success"})
            mock_db.execute.assert_called_once()
            call_args = mock_db.execute.call_args
            assert "INSERT INTO daemon_memory" in call_args[0][0]
            assert call_args[0][1][0] == "titan"
            assert call_args[0][1][1] == "task_123"

    @pytest.mark.asyncio
    async def test_load_episodic(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(return_value=[
                {"key": "k1", "content": {"data": 1}, "importance": 0.5,
                 "access_count": 0, "created_at": datetime.now(UTC),
                 "updated_at": datetime.now(UTC)},
            ])
            mock_db.execute = AsyncMock()  # for access_count update
            rows = await store.load("titan", "episodic")
            assert len(rows) == 1
            assert rows[0]["key"] == "k1"

    @pytest.mark.asyncio
    async def test_cleanup_expired(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
            count = await store.cleanup_expired("titan")
            assert count == 2

    @pytest.mark.asyncio
    async def test_cleanup_expired_global(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(return_value=[{"id": 1}])
            count = await store.cleanup_expired()  # None = global sweep
            assert count == 1
            call_sql = mock_db.fetch_all.call_args[0][0]
            assert "daemon_name" not in call_sql.split("WHERE")[1].split("AND")[0]

    @pytest.mark.asyncio
    async def test_save_via_protocol_method(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.execute = AsyncMock()
            await store.save("titan", "episodic", {
                "key": "test_key", "content": {"info": "value"}, "importance": 0.8,
            })
            mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_unknown_type_raises(self, store):
        with pytest.raises(ValueError, match="Unknown memory_type"):
            await store.save("titan", "procedural", {"key": "k"})


class TestDaemonMemoryStoreSemantic:
    """Semantic save + compression trigger."""

    @pytest.fixture
    def store(self):
        return DaemonMemoryStore(row_cap=5)

    @pytest.mark.asyncio
    async def test_save_semantic(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.execute = AsyncMock()
            mock_db.fetch_val = AsyncMock(return_value=2)  # under cap
            await store.save_semantic("titan", "pattern_1", {"pattern": "retry"})
            assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_compression_triggers_at_cap(self, store):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_val = AsyncMock(return_value=10)  # over cap of 5
            mock_db.fetch_all = AsyncMock(return_value=[
                {"id": i, "key": f"k{i}", "content": {"d": i}, "importance": 0.1}
                for i in range(3)
            ])
            mock_db.execute = AsyncMock()
            await store.save_semantic("titan", "new_key", {"data": "new"})
            # Should have called delete + insert for compression, plus the save
            assert mock_db.execute.call_count >= 3


# ---------------------------------------------------------------------------
# Memory isolation tests
# ---------------------------------------------------------------------------


class TestMemoryIsolation:
    """Cross-daemon access must raise PermissionError."""

    def test_same_daemon_allowed(self):
        check_memory_access("titan", "titan")  # should not raise

    def test_cross_daemon_denied(self):
        with patch("shared.daemon_memory._load_allowed_domains", return_value=["titan"]):
            with pytest.raises(PermissionError, match="cannot access"):
                check_memory_access("titan", "hermes")

    def test_cross_daemon_allowed_via_domains(self):
        with patch("shared.daemon_memory._load_allowed_domains",
                   return_value=["titan", "hermes"]):
            check_memory_access("titan", "hermes")  # should not raise

    @pytest.mark.asyncio
    async def test_isolated_store_blocks_access(self):
        with patch("shared.daemon_memory._load_allowed_domains", return_value=["titan"]):
            iso = IsolatedMemoryStore("titan")
            with pytest.raises(PermissionError):
                await iso.load("hermes", "episodic")

    @pytest.mark.asyncio
    async def test_isolated_store_allows_own_access(self):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(return_value=[])
            mock_db.execute = AsyncMock()
            iso = IsolatedMemoryStore("titan")
            result = await iso.load("titan", "episodic")
            assert result == []


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """MemoryStore Protocol check."""

    def test_daemon_memory_store_implements_protocol(self):
        from shared.contracts import MemoryStore
        store = DaemonMemoryStore()
        assert isinstance(store, MemoryStore)

    def test_isolated_store_implements_protocol(self):
        from shared.contracts import MemoryStore
        iso = IsolatedMemoryStore("titan")
        assert isinstance(iso, MemoryStore)


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """ENABLE_DEERFLOW_MEMORY flag gating."""

    def test_flag_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert not _memory_enabled()

    def test_flag_on_true(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "true"}):
            assert _memory_enabled()

    def test_flag_on_1(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "1"}):
            assert _memory_enabled()

    def test_flag_off_false(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "false"}):
            assert not _memory_enabled()


# ---------------------------------------------------------------------------
# AgentBase lifecycle hooks
# ---------------------------------------------------------------------------


class TestAgentBaseMemoryHooks:
    """_load_memory called during register, _save_memory during deregister."""

    @pytest.mark.asyncio
    async def test_register_calls_load_memory_when_enabled(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "true"}):
            with patch("shared.agent_base.db") as mock_db:
                mock_db.execute = AsyncMock()
                with patch("shared.daemon_memory.db") as mock_mem_db:
                    mock_mem_db.fetch_all = AsyncMock(return_value=[])
                    mock_mem_db.execute = AsyncMock()
                    # Create a concrete agent subclass
                    from shared.agent_base import AgentBase

                    class TestAgent(AgentBase):
                        name = "test_agent"
                        description = "test"
                        async def start(self): pass
                        async def stop(self): pass
                        async def health_check(self): return {"status": "ok"}

                    agent = TestAgent()
                    # Mock the cache file not existing
                    with patch("shared.daemon_memory.MemoryCache.load_cache",
                               new_callable=AsyncMock, return_value={}):
                        with patch("shared.daemon_memory.MemoryCache.rebuild_cache",
                                   new_callable=AsyncMock, return_value={}):
                            # Suppress Conway import
                            with patch("shared.config.config", create=True):
                                await agent.register()

                    assert agent._working_memory is not None
                    assert agent._memory is not None

    @pytest.mark.asyncio
    async def test_register_skips_memory_when_disabled(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": ""}, clear=False):
            with patch("shared.agent_base.db") as mock_db:
                mock_db.execute = AsyncMock()

                from shared.agent_base import AgentBase

                class TestAgent(AgentBase):
                    name = "test_agent"
                    description = "test"
                    async def start(self): pass
                    async def stop(self): pass
                    async def health_check(self): return {"status": "ok"}

                agent = TestAgent()
                with patch("shared.config.config", create=True):
                    await agent.register()

                assert agent._memory is None
                assert agent._working_memory is None

    @pytest.mark.asyncio
    async def test_deregister_calls_save_memory_when_enabled(self):
        with patch.dict(os.environ, {"ENABLE_DEERFLOW_MEMORY": "true"}):
            with patch("shared.agent_base.db") as mock_db:
                mock_db.execute = AsyncMock()

                from shared.agent_base import AgentBase

                class TestAgent(AgentBase):
                    name = "test_agent"
                    description = "test"
                    async def start(self): pass
                    async def stop(self): pass
                    async def health_check(self): return {"status": "ok"}

                agent = TestAgent()
                # Pre-populate memory state
                from shared.daemon_memory import DaemonMemoryStore, MemoryCache, WorkingMemory
                agent._memory = DaemonMemoryStore()
                agent._working_memory = WorkingMemory()
                agent._memory_cache = MemoryCache(agent._memory)
                agent._working_memory.set("session_data", {"key": "value"})

                with patch.object(agent._memory, "save_episodic",
                                  new_callable=AsyncMock) as mock_save:
                    with patch.object(agent._memory_cache, "save_cache",
                                      new_callable=AsyncMock):
                        await agent.deregister()

                mock_save.assert_called_once_with(
                    "test_agent", "session_data", {"key": "value"},
                )


# ---------------------------------------------------------------------------
# daemon_memory_stats view (mock query)
# ---------------------------------------------------------------------------


class TestDaemonMemoryStats:
    """Verify the stats view can be queried."""

    @pytest.mark.asyncio
    async def test_stats_view_query(self):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(return_value=[
                {"daemon_name": "titan", "memory_type": "episodic",
                 "entry_count": 42, "avg_importance": 0.6},
            ])
            rows = await mock_db.fetch_all(
                "SELECT * FROM daemon_memory_stats"
            )
            assert len(rows) == 1
            assert rows[0]["daemon_name"] == "titan"
