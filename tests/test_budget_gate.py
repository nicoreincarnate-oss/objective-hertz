"""Tests for consolidated budget routing via check_budget_for_llm_call.

Budget enforcement lives in shared/middleware.py:check_budget_for_llm_call.
These tests verify LLMClient.generate routes correctly through the consolidated path.
"""

import asyncio
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


def _run(coro):
    return asyncio.run(coro)


class TestConsolidatedBudgetRouting:
    """Test LLMClient routes through check_budget_for_llm_call correctly."""

    @staticmethod
    def _set_api_key(cfg, val):
        """Set api_key on frozen dataclass."""
        object.__setattr__(cfg.claude, "api_key", val)

    @pytest.mark.asyncio
    async def test_generate_routes_to_ollama_when_budget_returns_local(self, llm):
        """When check_budget_for_llm_call returns 'local', generate uses Ollama."""
        llm._ollama_generate = AsyncMock(return_value="ollama_response")
        llm._claude_generate = AsyncMock(return_value="claude_response")
        llm._record_claude_spend = AsyncMock()

        import shared.middleware as mw_mod
        from shared.config import config as cfg
        old_key = cfg.claude.api_key
        try:
            self._set_api_key(cfg, "sk-test")
            with patch.object(mw_mod, "check_budget_for_llm_call", AsyncMock(return_value="local")):
                result = await llm.generate("hello", model="fast")
        finally:
            self._set_api_key(cfg, old_key)
        assert result == "ollama_response"
        llm._ollama_generate.assert_awaited_once()
        llm._claude_generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generate_uses_claude_when_budget_returns_smart(self, llm):
        """When check_budget_for_llm_call returns 'smart', generate uses Claude."""
        llm._ollama_generate = AsyncMock(return_value="ollama_response")
        llm._claude_generate = AsyncMock(return_value="claude_response")
        llm._record_claude_spend = AsyncMock()

        import shared.middleware as mw_mod
        from shared.config import config as cfg
        old_key = cfg.claude.api_key
        try:
            self._set_api_key(cfg, "sk-test")
            with patch.object(mw_mod, "check_budget_for_llm_call", AsyncMock(return_value="smart")):
                result = await llm.generate("hello", model="smart")
        finally:
            self._set_api_key(cfg, old_key)
        assert result == "claude_response"
        llm._claude_generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_uses_claude_when_budget_returns_fast(self, llm):
        """When check_budget_for_llm_call returns 'fast', generate uses Claude."""
        llm._ollama_generate = AsyncMock(return_value="ollama_response")
        llm._claude_generate = AsyncMock(return_value="claude_response")
        llm._record_claude_spend = AsyncMock()

        import shared.middleware as mw_mod
        from shared.config import config as cfg
        old_key = cfg.claude.api_key
        try:
            self._set_api_key(cfg, "sk-test")
            with patch.object(mw_mod, "check_budget_for_llm_call", AsyncMock(return_value="fast")):
                result = await llm.generate("hello", model="fast")
        finally:
            self._set_api_key(cfg, old_key)
        assert result == "claude_response"
        llm._claude_generate.assert_awaited_once()

    def test_check_budget_for_llm_call_under_budget(self):
        """Direct test: under budget returns requested model."""
        from shared.middleware import check_budget_for_llm_call

        mock_fetch = AsyncMock(return_value=100)
        fake_db = types.ModuleType("shared.db")
        fake_db.fetch_val = mock_fetch

        with patch.dict(os.environ, {"ENABLE_CONSOLIDATED_BUDGET": "true"}):
            with patch.dict(sys.modules, {"shared.db": fake_db}):
                result = _run(check_budget_for_llm_call("fast"))
        assert result == "fast"

    def test_check_budget_for_llm_call_exceeded(self):
        """Direct test: exceeded budget returns 'local'."""
        from shared.middleware import check_budget_for_llm_call

        mock_fetch = AsyncMock(return_value=850)
        fake_db = types.ModuleType("shared.db")
        fake_db.fetch_val = mock_fetch

        with patch.dict(os.environ, {"ENABLE_CONSOLIDATED_BUDGET": "true"}):
            with patch.dict(sys.modules, {"shared.db": fake_db}):
                result = _run(check_budget_for_llm_call("smart"))
        assert result == "local"

    def test_check_budget_for_llm_call_db_failure_fails_closed(self):
        """Direct test: DB failure returns 'local' (fail-closed)."""
        from shared.middleware import check_budget_for_llm_call

        mock_fetch = AsyncMock(side_effect=Exception("DB down"))
        fake_db = types.ModuleType("shared.db")
        fake_db.fetch_val = mock_fetch

        with patch.dict(os.environ, {"ENABLE_CONSOLIDATED_BUDGET": "true"}):
            with patch.dict(sys.modules, {"shared.db": fake_db}):
                result = _run(check_budget_for_llm_call("fast"))
        assert result == "local"
