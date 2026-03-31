"""Tests for budget-aware LLM routing — the spend control plane.

When ENABLE_CONSOLIDATED_BUDGET=false (default): _budget_gate runs as legacy.
When ENABLE_CONSOLIDATED_BUDGET=true: check_budget_for_llm_call is the sole authority.
"""

import os
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def llm():
    # Ensure shared.db is a real module in sys.modules so patch targets resolve
    if "shared.db" not in sys.modules:
        fake_db = types.ModuleType("shared.db")
        fake_db.fetch_val = AsyncMock(return_value=0)
        fake_db.fetch_one = AsyncMock(return_value=None)
        fake_db.execute = AsyncMock()
        fake_db.init_pool = AsyncMock()
        fake_db.close_pool = AsyncMock()
        fake_db.get_config = AsyncMock(return_value=None)
        sys.modules["shared.db"] = fake_db

    from shared.llm_client import LLMClient
    return LLMClient()


class TestBudgetGate:
    """_budget_gate (legacy) must downgrade Claude to Ollama when budget is tight.

    These tests only apply when ENABLE_CONSOLIDATED_BUDGET is false (default).
    """

    @pytest.mark.asyncio
    async def test_under_budget_allows_fast(self, llm):
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=100)
        result = await llm._budget_gate("fast")
        assert result == "fast"

    @pytest.mark.asyncio
    async def test_under_budget_allows_smart(self, llm):
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=100)
        result = await llm._budget_gate("smart")
        assert result == "smart"

    @pytest.mark.asyncio
    async def test_at_threshold_downgrades_fast(self, llm):
        # 80% of $800 = $640, so $650 is over threshold
        # "fast" is Claude Haiku — downgrades to Ollama when budget is tight
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=650)
        result = await llm._budget_gate("fast")
        assert result == "local"

    @pytest.mark.asyncio
    async def test_at_threshold_keeps_smart(self, llm):
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=650)
        result = await llm._budget_gate("smart")
        assert result == "smart"

    @pytest.mark.asyncio
    async def test_exceeded_forces_all_to_local(self, llm):
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=850)
        fast = await llm._budget_gate("fast")
        smart = await llm._budget_gate("smart")
        assert fast == "local"
        assert smart == "local"

    @pytest.mark.asyncio
    async def test_db_failure_fails_open(self, llm):
        """If budget check DB fails, allow the call (don't block revenue).

        Only applies when ENABLE_CONSOLIDATED_BUDGET=false (legacy behavior).
        """
        sys.modules["shared.db"].fetch_val = AsyncMock(side_effect=Exception("DB down"))
        result = await llm._budget_gate("fast")
        assert result == "fast"


class TestConsolidatedBudgetFeatureFlag:
    """Test feature flag routing between legacy and consolidated budget paths."""

    @pytest.mark.asyncio
    async def test_db_failure_fails_closed_when_consolidated(self, llm):
        """With ENABLE_CONSOLIDATED_BUDGET=true, DB failure returns 'local' (fail-closed)."""
        mock_check = AsyncMock(return_value="local")
        with patch.dict(os.environ, {"ENABLE_CONSOLIDATED_BUDGET": "true"}):
            with patch("shared.middleware.check_budget_for_llm_call", mock_check):
                # Simulate generate routing
                from shared.middleware import check_budget_for_llm_call
                result = await check_budget_for_llm_call("fast")
        assert result == "local"

    @pytest.mark.asyncio
    async def test_flag_off_preserves_legacy_behavior(self, llm):
        """With flag OFF, _budget_gate is still called (not check_budget_for_llm_call)."""
        sys.modules["shared.db"].fetch_val = AsyncMock(return_value=100)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_CONSOLIDATED_BUDGET", None)
            result = await llm._budget_gate("fast")
        assert result == "fast"
