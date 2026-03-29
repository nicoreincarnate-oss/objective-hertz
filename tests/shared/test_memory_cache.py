"""Tests for DeerFlow MemoryCache (JSON file cache for fast startup).

Covers:
- Round-trip: save then load returns same data
- Startup time: cache load is fast (no DB calls)
- Corrupt cache handling
- Write-through: save_with_cache writes to both DB and cache
- Cache invalidation
- Cache rebuild from Postgres
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shared.daemon_memory import DaemonMemoryStore, MemoryCache


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Override MemoryCache.CACHE_DIR to use tmp_path."""
    original = MemoryCache.CACHE_DIR
    MemoryCache.CACHE_DIR = tmp_path
    yield tmp_path
    MemoryCache.CACHE_DIR = original


@pytest.fixture
def cache(tmp_cache_dir):
    """MemoryCache with mocked store and temp directory."""
    store = DaemonMemoryStore()
    return MemoryCache(store)


class TestMemoryCacheRoundTrip:
    """JSON cache round-trip: save then load."""

    @pytest.mark.asyncio
    async def test_save_and_load(self, cache, tmp_cache_dir):
        data = {"task_history": [1, 2, 3], "last_lead": "acme"}
        await cache.save_cache("titan", data)

        loaded = await cache.load_cache("titan")
        assert loaded == data

    @pytest.mark.asyncio
    async def test_load_empty_returns_dict(self, cache):
        loaded = await cache.load_cache("nonexistent_daemon")
        assert loaded == {}

    @pytest.mark.asyncio
    async def test_corrupt_cache_returns_empty(self, cache, tmp_cache_dir):
        # Write invalid JSON
        cache_file = tmp_cache_dir / "titan.json"
        cache_file.write_text("{broken json!!!")
        loaded = await cache.load_cache("titan")
        assert loaded == {}

    @pytest.mark.asyncio
    async def test_overwrite_existing(self, cache, tmp_cache_dir):
        await cache.save_cache("titan", {"v": 1})
        await cache.save_cache("titan", {"v": 2})
        loaded = await cache.load_cache("titan")
        assert loaded == {"v": 2}


class TestMemoryCacheStartupTime:
    """Cache load should not make any DB calls."""

    @pytest.mark.asyncio
    async def test_load_does_not_call_db(self, cache, tmp_cache_dir):
        await cache.save_cache("titan", {"fast": True})
        with patch("shared.daemon_memory.db") as mock_db:
            loaded = await cache.load_cache("titan")
            mock_db.fetch_all.assert_not_called()
            mock_db.execute.assert_not_called()
        assert loaded == {"fast": True}


class TestWriteThrough:
    """save_with_cache writes to Postgres AND updates local JSON."""

    @pytest.mark.asyncio
    async def test_save_with_cache(self, cache, tmp_cache_dir):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.execute = AsyncMock()
            await cache.save_with_cache(
                "titan", "lead_42", {"status": "contacted"},
                memory_type="episodic", importance=0.7,
            )
            # DB was called
            mock_db.execute.assert_called_once()

        # Cache was updated
        loaded = await cache.load_cache("titan")
        assert loaded["lead_42"] == {"status": "contacted"}


class TestCacheInvalidation:
    """Cache invalidation deletes the local file."""

    @pytest.mark.asyncio
    async def test_invalidate_removes_file(self, cache, tmp_cache_dir):
        await cache.save_cache("titan", {"data": 1})
        cache_file = tmp_cache_dir / "titan.json"
        assert cache_file.exists()

        await cache.invalidate("titan")
        assert not cache_file.exists()

    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_is_noop(self, cache, tmp_cache_dir):
        await cache.invalidate("nonexistent")  # should not raise


class TestCacheRebuild:
    """Rebuild cache from Postgres (authoritative source)."""

    @pytest.mark.asyncio
    async def test_rebuild_cache(self, cache, tmp_cache_dir):
        with patch("shared.daemon_memory.db") as mock_db:
            mock_db.fetch_all = AsyncMock(side_effect=[
                # episodic load
                [{"key": "e1", "content": {"episodic": True}, "importance": 0.5,
                  "access_count": 1, "created_at": None, "updated_at": None}],
                # access_count update (fire-and-forget)
                # semantic load
                [{"key": "s1", "content": {"semantic": True}, "importance": 0.8,
                  "access_count": 3, "created_at": None, "updated_at": None}],
            ])
            mock_db.execute = AsyncMock()

            result = await cache.rebuild_cache("titan")
            assert "e1" in result
            assert "s1" in result
            assert result["e1"] == {"episodic": True}

        # Verify it was also written to disk
        loaded = await cache.load_cache("titan")
        assert "e1" in loaded
