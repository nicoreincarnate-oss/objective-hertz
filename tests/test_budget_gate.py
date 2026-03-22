"""Tests for budget-aware LLM routing — the spend control plane."""

import sys
import types
from unittest.mock import AsyncMock

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
        sys.modules["shared.db"] = fake_db

    from shared.llm_client import LLMClient
    return LLMClient()


class TestBudgetGate:
    """_budget_gate must downgrade Claude to Ollama when budget is tight."""

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
        """If budget check DB fails, allow the call (don't block revenue)."""
        sys.modules["shared.db"].fetch_val = AsyncMock(side_effect=Exception("DB down"))
        result = await llm._budget_gate("fast")
        assert result == "fast"
