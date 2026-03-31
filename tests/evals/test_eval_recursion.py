"""Behavioral eval: recursion guard blocks excessive task depth.

The recursion guard in AgentBase (Phase 12: FP-03) must reject tasks
whose depth exceeds the configured max_task_depth.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_test_agent():
    """Create a minimal concrete AgentBase subclass for testing."""
    from shared.agent_base import AgentBase

    class TestAgent(AgentBase):
        name = "test_agent"
        description = "test"

        async def start(self):
            pass

        async def stop(self):
            pass

        async def health_check(self):
            return {"status": "ok"}

    return TestAgent()


@pytest.mark.asyncio
async def test_recursion_guard_blocks_deep_tasks(eval_recorder):
    """Task claim must be rejected when depth exceeds max_task_depth."""
    import shared.agent_base as agent_base_mod

    agent = _make_test_agent()
    max_depth = 5

    mock_db = MagicMock()
    mock_db.get_config = AsyncMock(return_value=max_depth)
    mock_db.fetch_one = AsyncMock(return_value={"depth": max_depth + 1})
    mock_db.execute = AsyncMock()
    mock_db.emit_event = AsyncMock()
    mock_db.fetch_all = AsyncMock(return_value=[])

    with patch.dict("os.environ", {"RECURSION_GUARD_ENABLED": "true"}), \
         patch.object(agent_base_mod, "db", mock_db):
        result = await agent.claim_task(999)
        assert result is False, (
            f"Recursion guard MUST block tasks at depth > {max_depth}. "
            "Unbounded recursion risks infinite loops and budget exhaustion."
        )

    await eval_recorder.record(
        suite="recursion",
        scenario="blocks_depth_gt_max",
        passed=True,
    )


@pytest.mark.asyncio
async def test_recursion_guard_allows_shallow_tasks(eval_recorder):
    """Tasks within recursion limit should be claimable."""
    import shared.agent_base as agent_base_mod

    agent = _make_test_agent()

    mock_db = MagicMock()
    mock_db.get_config = AsyncMock(return_value=5)
    # Two fetch_one calls: depth check, then UPDATE RETURNING
    mock_db.fetch_one = AsyncMock(side_effect=[
        {"depth": 2},                    # recursion depth check
        {"id": 1, "status": "running"},  # UPDATE RETURNING (claim succeeded)
    ])
    mock_db.execute = AsyncMock()
    mock_db.emit_event = AsyncMock()
    mock_db.fetch_all = AsyncMock(return_value=[])

    with patch.dict("os.environ", {"RECURSION_GUARD_ENABLED": "true"}), \
         patch.object(agent_base_mod, "db", mock_db):
        result = await agent.claim_task(1)
        assert result is True, (
            "Tasks within recursion limit should be claimable."
        )

    await eval_recorder.record(
        suite="recursion",
        scenario="allows_shallow_tasks",
        passed=True,
    )
