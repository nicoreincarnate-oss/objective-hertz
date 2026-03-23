"""Tests for task queue deduplication — prevents runaway spend from duplicate work."""

import pytest

from tests.conftest import FakeDB


@pytest.fixture
def db():
    return FakeDB()


class TestInsertTaskDedupe:
    """insert_task must not create duplicates of pending/running tasks."""

    @pytest.mark.asyncio
    async def test_first_insert_succeeds(self, db):
        task_id = await db.insert_task("lead_discovery")
        assert task_id is not None
        assert task_id == 1

    @pytest.mark.asyncio
    async def test_duplicate_pending_blocked(self, db):
        first = await db.insert_task("lead_discovery")
        second = await db.insert_task("lead_discovery")
        assert first is not None
        assert second is None
        assert len(db.tables["task_queue"]) == 1

    @pytest.mark.asyncio
    async def test_different_types_allowed(self, db):
        t1 = await db.insert_task("lead_discovery")
        t2 = await db.insert_task("email_compose")
        assert t1 is not None
        assert t2 is not None
        assert len(db.tables["task_queue"]) == 2

    @pytest.mark.asyncio
    async def test_completed_task_allows_new_insert(self, db):
        await db.insert_task("lead_discovery")
        # Simulate completion
        db.tables["task_queue"][0]["status"] = "completed"
        second = await db.insert_task("lead_discovery")
        assert second is not None
        assert len(db.tables["task_queue"]) == 2

    @pytest.mark.asyncio
    async def test_failed_task_allows_new_insert(self, db):
        await db.insert_task("lead_discovery")
        db.tables["task_queue"][0]["status"] = "failed"
        second = await db.insert_task("lead_discovery")
        assert second is not None

    @pytest.mark.asyncio
    async def test_running_task_blocks_new_insert(self, db):
        await db.insert_task("lead_discovery")
        db.tables["task_queue"][0]["status"] = "running"
        second = await db.insert_task("lead_discovery")
        assert second is None

    @pytest.mark.asyncio
    async def test_ten_rapid_inserts_only_one_survives(self, db):
        """Simulates Perseus scheduling the same task 10 times before Titan picks it up."""
        results = []
        for _ in range(10):
            results.append(await db.insert_task("lead_discovery"))
        assert results.count(None) == 9
        assert len(db.tables["task_queue"]) == 1

    @pytest.mark.asyncio
    async def test_daemon_requests_can_bypass_dedupe(self, db):
        first = await db.insert_task("web_scrape", {"url": "https://a.example"}, dedupe=False)
        second = await db.insert_task("web_scrape", {"url": "https://b.example"}, dedupe=False)
        assert first is not None
        assert second is not None
        assert len(db.tables["task_queue"]) == 2
