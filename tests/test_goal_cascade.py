"""Tests for shared/goal_cascade.py — Goal Cascade hierarchy."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest

# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    """Enable the GOAL_CASCADE_ENABLED flag for all tests by default."""
    monkeypatch.setenv("GOAL_CASCADE_ENABLED", "true")


@pytest.fixture
def mock_db():
    """Mock shared.db module functions."""
    with patch("shared.goal_cascade.db") as db:
        db.execute = AsyncMock()
        db.fetch_one = AsyncMock(return_value=None)
        db.fetch_all = AsyncMock(return_value=[])
        yield db


# ── Tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_goal_returns_uuid(mock_db):
    """create_goal returns a valid UUID string."""
    from shared.goal_cascade import create_goal

    result = await create_goal("company", "Test goal")
    assert result is not None
    # Should be a valid UUID
    uuid.UUID(result)
    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args[0]
    assert "INSERT INTO goals" in call_args[0]


@pytest.mark.asyncio
async def test_create_goal_invalid_level_raises(mock_db):
    """Invalid level raises ValueError."""
    from shared.goal_cascade import create_goal

    with pytest.raises(ValueError, match="Invalid goal level"):
        await create_goal("invalid", "Test goal")


@pytest.mark.asyncio
async def test_resolve_goal_cascade_order(mock_db):
    """Agent-level goal takes priority over team-level for matching agent."""
    from shared.goal_cascade import resolve_goal

    agent_goal = {"goal_id": "agent-001", "level": "agent", "status": "active"}

    # First call (task-level) returns None, second call (agent-level) returns match
    mock_db.fetch_one = AsyncMock(side_effect=[None, agent_goal])

    result = await resolve_goal("lead_discovery", assigned_agent="titan")
    assert result is not None
    assert result["goal_id"] == "agent-001"
    assert result["level"] == "agent"


@pytest.mark.asyncio
async def test_resolve_goal_fallback_to_company(mock_db):
    """Unknown task_type with no agent falls back to company goal."""
    from shared.goal_cascade import resolve_goal

    company_goal = {"goal_id": "company-001", "level": "company", "status": "active"}

    # task=None, team=None, company=match
    mock_db.fetch_one = AsyncMock(side_effect=[None, None, company_goal])

    result = await resolve_goal("unknown_task")
    assert result is not None
    assert result["level"] == "company"


@pytest.mark.asyncio
async def test_update_goal_progress_aggregation(mock_db):
    """2/4 children completed = 50.0% completion."""
    from shared.goal_cascade import update_goal_progress

    mock_db.fetch_all = AsyncMock(return_value=[
        {"status": "completed", "cnt": 2},
        {"status": "active", "cnt": 2},
    ])

    result = await update_goal_progress("parent-001")
    assert result["total_children"] == 4
    assert result["completed_children"] == 2
    assert result["completion_pct"] == 50.0
    assert result["child_statuses"] == {"completed": 2, "active": 2}


@pytest.mark.asyncio
async def test_get_goal_tree_structure(mock_db):
    """Tree has company root with team children."""
    from shared.goal_cascade import get_goal_tree

    mock_db.fetch_all = AsyncMock(return_value=[
        {"goal_id": "c1", "level": "company", "parent_id": None, "description": "Company",
         "success_criteria": None, "status": "active", "assigned_agent": None,
         "created_at": "2026-01-01", "updated_at": "2026-01-01"},
        {"goal_id": "t1", "level": "team", "parent_id": "c1", "description": "Team",
         "success_criteria": None, "status": "active", "assigned_agent": None,
         "created_at": "2026-01-01", "updated_at": "2026-01-01"},
        {"goal_id": "a1", "level": "agent", "parent_id": "t1", "description": "Agent",
         "success_criteria": None, "status": "active", "assigned_agent": "titan",
         "created_at": "2026-01-01", "updated_at": "2026-01-01"},
    ])

    tree = await get_goal_tree()
    assert len(tree) == 1  # 1 root
    assert tree[0]["goal_id"] == "c1"
    assert len(tree[0]["children"]) == 1  # team child
    assert tree[0]["children"][0]["goal_id"] == "t1"
    assert len(tree[0]["children"][0]["children"]) == 1  # agent grandchild


@pytest.mark.asyncio
async def test_complete_goal_updates_status(mock_db):
    """complete_goal changes status to completed."""
    from shared.goal_cascade import complete_goal

    mock_db.fetch_one = AsyncMock(return_value={"goal_id": "g1"})

    result = await complete_goal("g1")
    assert result is True
    call_args = mock_db.fetch_one.call_args[0]
    assert "completed" in call_args[0]


@pytest.mark.asyncio
async def test_feature_flag_off_returns_none(monkeypatch, mock_db):
    """All functions return None/empty when GOAL_CASCADE_ENABLED is off."""
    monkeypatch.setenv("GOAL_CASCADE_ENABLED", "false")
    from shared.goal_cascade import create_goal, resolve_goal, get_goal_tree, complete_goal, fail_goal

    assert await create_goal("company", "Test") is None
    assert await resolve_goal("test") is None
    assert await get_goal_tree() == []
    assert await complete_goal("id") is False
    assert await fail_goal("id") is False
    # DB should never be called
    mock_db.execute.assert_not_called()
    mock_db.fetch_one.assert_not_called()
    mock_db.fetch_all.assert_not_called()
