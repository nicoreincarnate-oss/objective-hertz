"""Tests for Recursion Guard (Phase 12: FP-03)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# insert_task depth parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_zero_default():
    """New tasks inserted with depth=0 by default."""
    with patch("shared.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = [None, {"id": 42}]  # dedupe check, insert
        with patch("shared.db.enrich_payload_with_context", side_effect=lambda p: p):
            from shared.db import insert_task

            result = await insert_task("test_type", {"key": "val"}, priority=5)
            # The INSERT call should include depth=0
            insert_call = mock_fetch.call_args_list[1]
            query = insert_call[0][0]
            params = insert_call[0][1]
            assert "depth" in query
            assert params[3] == 0  # depth param


@pytest.mark.asyncio
async def test_depth_propagation():
    """spawn_child_task sets depth = parent + 1."""
    with patch.dict(os.environ, {}, clear=False):
        from shared.agent_base import AgentBase

        class _TestAgent(AgentBase):
            name = "depth_test"
            description = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def health_check(self):
                return {}

        agent = _TestAgent()

        with patch("shared.db.fetch_one", new_callable=AsyncMock) as mock_fetch:
            # First call: parent depth lookup returns depth=2
            # Second call (inside insert_task): dedupe check returns None
            # Third call (inside insert_task): insert returns id
            mock_fetch.side_effect = [
                {"depth": 2},
                None,
                {"id": 99},
            ]
            with patch("shared.db.enrich_payload_with_context", side_effect=lambda p: p):
                result = await agent.spawn_child_task(
                    parent_task_id=10,
                    task_type="child_type",
                    payload={"data": "value"},
                )
                assert result == 99
                # The insert call should have depth=3
                insert_call = mock_fetch.call_args_list[2]
                params = insert_call[0][1]
                assert params[3] == 3  # parent_depth(2) + 1


@pytest.mark.asyncio
async def test_max_depth_rejection():
    """Task with depth > max is rejected and failed."""
    with patch.dict(os.environ, {"RECURSION_GUARD_ENABLED": "true"}, clear=False):
        from shared.agent_base import AgentBase

        class _TestAgent(AgentBase):
            name = "depth_reject"
            description = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def health_check(self):
                return {}

        agent = _TestAgent()

        with patch("shared.db.get_config", new_callable=AsyncMock, return_value=5):
            with patch("shared.db.fetch_one", new_callable=AsyncMock, return_value={"depth": 10}):
                with patch.object(agent, "fail_task", new_callable=AsyncMock) as mock_fail:
                    result = await agent.claim_task(42)
                    assert result is False
                    mock_fail.assert_called_once()
                    assert "recursion_guard" in mock_fail.call_args[0][1]


@pytest.mark.asyncio
async def test_configurable_max_depth():
    """system_config max_task_depth overrides default 5."""
    with patch.dict(os.environ, {"RECURSION_GUARD_ENABLED": "true"}, clear=False):
        from shared.agent_base import AgentBase

        class _TestAgent(AgentBase):
            name = "depth_config"
            description = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def health_check(self):
                return {}

        agent = _TestAgent()

        # max_depth = 3, task depth = 4 => rejected
        with patch("shared.db.get_config", new_callable=AsyncMock, return_value=3):
            with patch("shared.db.fetch_one", new_callable=AsyncMock, return_value={"depth": 4}):
                with patch.object(agent, "fail_task", new_callable=AsyncMock):
                    result = await agent.claim_task(42)
                    assert result is False


@pytest.mark.asyncio
async def test_feature_flag_off_skips_check():
    """Depth check skipped when flag is off."""
    with patch.dict(os.environ, {"RECURSION_GUARD_ENABLED": ""}, clear=False):
        from shared.agent_base import AgentBase

        class _TestAgent(AgentBase):
            name = "depth_off"
            description = "test"

            async def start(self):
                pass

            async def stop(self):
                pass

            async def health_check(self):
                return {}

        agent = _TestAgent()

        # Even with depth=100, claim should proceed normally when flag is off
        with patch("shared.db.fetch_one", new_callable=AsyncMock, return_value={"id": 42}):
            with patch("shared.agent_base.record_task_claimed"):
                with patch.object(agent, "_transition", new_callable=AsyncMock, return_value=True):
                    result = await agent.claim_task(42)
                    assert result is True
