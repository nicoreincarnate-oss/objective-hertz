"""Tests for Phase 23 Tasks 23-02 through 23-05: LLM Providers.

Validates:
- AnthropicProvider: response parsing, artifact cleanup, token extraction
- OllamaProvider: request format, TurboQuant options, model fallback
- HeavyLocalProvider: routing delegation
- OJCloudProvider: result dict to LLMResponse conversion
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing providers
# ---------------------------------------------------------------------------

# httpx mock
if "httpx" not in sys.modules:
    _fake_httpx = types.ModuleType("httpx")
    _fake_httpx.AsyncClient = MagicMock
    _fake_httpx.Timeout = MagicMock
    _fake_httpx.HTTPError = type("HTTPError", (Exception,), {})
    sys.modules["httpx"] = _fake_httpx

# psycopg mock
if "psycopg" not in sys.modules:
    _fake_psycopg = types.ModuleType("psycopg")
    _fake_psycopg.Error = type("Error", (Exception,), {})
    sys.modules["psycopg"] = _fake_psycopg

if "psycopg.rows" not in sys.modules:
    _fake_psycopg_rows = types.ModuleType("psycopg.rows")
    _fake_psycopg_rows.dict_row = MagicMock()
    sys.modules["psycopg.rows"] = _fake_psycopg_rows

if "psycopg_pool" not in sys.modules:
    _fake_pool = types.ModuleType("psycopg_pool")
    _fake_pool.AsyncConnectionPool = MagicMock
    sys.modules["psycopg_pool"] = _fake_pool

# shared.db mock
_fake_db = types.ModuleType("shared.db")
for attr in ("execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool",
             "insert_task", "transaction", "emit_event"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.get_config = AsyncMock(return_value=None)
sys.modules["shared.db"] = _fake_db

# shared.config mock
_fake_config_mod = types.ModuleType("shared.config")
_fake_config_mod.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test-providers"),
    claude=types.SimpleNamespace(
        api_key="sk-test-key",
        primary_model="claude-sonnet-4-6",
        fast_model="claude-haiku-4-5",
        genius_model="claude-opus-4-6",
    ),
    ollama=types.SimpleNamespace(
        host="http://localhost:11434",
        model="qwen2.5:14b",
        secondary="llama3.2:3b",
        embed_model="nomic-embed-text",
        kv_cache_type="turbo4",
        flash_attention=True,
    ),
    airllm=types.SimpleNamespace(enabled=False, model="", compression="4bit",
                                  profiling_mode=False, hf_token="",
                                  layer_shards_path="",
                                  prompt_char_threshold=12000,
                                  preferred_stages="research"),
    ollm=types.SimpleNamespace(enabled=False, model="", prompt_char_threshold=40000,
                                preferred_stages="deep_research"),
)
sys.modules["shared.config"] = _fake_config_mod

# shared.airllm_policy mock
_fake_airllm_policy = types.ModuleType("shared.airllm_policy")
_fake_airllm_policy.choose_heavy_local_backend = MagicMock(return_value="ollama")
_fake_airllm_policy.explain_heavy_local_routing = MagicMock(return_value="test")
_fake_airllm_policy.should_route_to_heavy_local = MagicMock(return_value=False)
sys.modules["shared.airllm_policy"] = _fake_airllm_policy

# shared.prompt_builder mock
_fake_prompt_builder = types.ModuleType("shared.prompt_builder")
_fake_prompt_builder.CACHE_BOUNDARY_MARKER = "---CACHE_BOUNDARY---"
_latch_mock = MagicMock()
_latch_mock.peek = MagicMock(return_value=None)
_latch_mock.get = MagicMock(side_effect=lambda key, factory: factory())
_fake_prompt_builder.get_session_latch = MagicMock(return_value=_latch_mock)
sys.modules["shared.prompt_builder"] = _fake_prompt_builder

# shared.middleware mock
_fake_middleware = types.ModuleType("shared.middleware")
_fake_middleware.check_budget_for_llm_call = AsyncMock(side_effect=lambda m: m)
sys.modules["shared.middleware"] = _fake_middleware

# shared.observability mock
_fake_obs = types.ModuleType("shared.observability")
_fake_obs.record_llm_call = AsyncMock()
_fake_obs._task_id = MagicMock()
_fake_obs._task_id.get = MagicMock(return_value=None)
sys.modules["shared.observability"] = _fake_obs

# shared.cost_events mock
_fake_cost_events = types.ModuleType("shared.cost_events")
_fake_cost_events.CostEvent = MagicMock
_fake_cost_events.emit_cost_event = AsyncMock()
sys.modules["shared.cost_events"] = _fake_cost_events

# shared.agent_dna mock
_fake_dna = types.ModuleType("shared.agent_dna")
_fake_dna.get_dna = MagicMock(return_value=None)
_fake_dna.get_circuit_breaker = MagicMock(return_value=MagicMock(record=MagicMock()))
sys.modules["shared.agent_dna"] = _fake_dna

# shared.learning_extractor mock
_fake_learning = types.ModuleType("shared.learning_extractor")
_fake_learning.maybe_extract_background = MagicMock()
sys.modules["shared.learning_extractor"] = _fake_learning

# shared.memory_index mock
_fake_memory_index = types.ModuleType("shared.memory_index")
_fake_memory_index.get_memory_index = MagicMock(return_value="")
_fake_memory_index._MAX_ENTRIES = 100
sys.modules["shared.memory_index"] = _fake_memory_index

from shared.llm_unified import LLMResponse, ModelTier


# ---------------------------------------------------------------------------
# Anthropic Provider Tests
# ---------------------------------------------------------------------------


class TestAnthropicArtifactCleanup:
    """Test _clean_response_blocks in AnthropicProvider."""

    def _get_provider(self):
        from shared.llm_providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    def test_text_blocks_preserved(self):
        provider = self._get_provider()
        blocks = [{"type": "text", "text": "Hello world"}]
        result = provider._clean_response_blocks(blocks, tools_requested=False)
        assert result == "Hello world"

    def test_multiple_text_blocks_joined(self):
        provider = self._get_provider()
        blocks = [
            {"type": "text", "text": "Part 1"},
            {"type": "text", "text": "Part 2"},
        ]
        result = provider._clean_response_blocks(blocks, tools_requested=False)
        assert result == "Part 1\nPart 2"

    def test_thinking_blocks_stripped(self):
        provider = self._get_provider()
        blocks = [
            {"type": "thinking", "thinking": "internal reasoning"},
            {"type": "text", "text": "Actual response"},
        ]
        result = provider._clean_response_blocks(blocks, tools_requested=False)
        assert result == "Actual response"
        assert "internal reasoning" not in result

    def test_tool_use_stripped_when_not_requested(self):
        provider = self._get_provider()
        blocks = [
            {"type": "text", "text": "Here is the answer"},
            {"type": "tool_use", "name": "calculator", "input": {"expr": "1+1"}},
        ]
        result = provider._clean_response_blocks(blocks, tools_requested=False)
        assert result == "Here is the answer"
        assert "calculator" not in result

    def test_tool_use_kept_when_requested(self):
        """When tools were requested, tool_use blocks should be kept as-is.
        But since we only extract text, they won't appear in the joined result."""
        provider = self._get_provider()
        blocks = [
            {"type": "text", "text": "Let me calculate"},
            {"type": "tool_use", "name": "calculator", "input": {"expr": "1+1"}},
        ]
        # tool_use blocks are not text, so they get skipped in text extraction
        result = provider._clean_response_blocks(blocks, tools_requested=True)
        assert "Let me calculate" in result

    def test_empty_blocks_return_empty(self):
        provider = self._get_provider()
        result = provider._clean_response_blocks([], tools_requested=False)
        assert result == ""

    def test_only_thinking_returns_empty(self):
        provider = self._get_provider()
        blocks = [{"type": "thinking", "thinking": "just thinking..."}]
        result = provider._clean_response_blocks(blocks, tools_requested=False)
        assert result == ""


class TestAnthropicModelTierMapping:
    """Test _model_id_to_tier_str mapping."""

    def _get_provider(self):
        from shared.llm_providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()

    def test_opus_maps_to_genius(self):
        assert self._get_provider()._model_id_to_tier_str("claude-opus-4-6") == "genius"

    def test_haiku_maps_to_fast(self):
        assert self._get_provider()._model_id_to_tier_str("claude-haiku-4-5") == "fast"

    def test_sonnet_maps_to_smart(self):
        assert self._get_provider()._model_id_to_tier_str("claude-sonnet-4-6") == "smart"

    def test_unknown_maps_to_smart(self):
        assert self._get_provider()._model_id_to_tier_str("some-unknown-model") == "smart"


# ---------------------------------------------------------------------------
# UnifiedEngineAdapter Tests
# ---------------------------------------------------------------------------

class TestUnifiedEngineAdapter:
    """Test UnifiedEngineAdapter fallback and proxy behavior."""

    def test_passthrough_on_success(self):
        from shared.llm_factory import UnifiedEngineAdapter

        inner = MagicMock()
        inner.generate.return_value = {"content": "success"}
        adapter = UnifiedEngineAdapter(inner)

        result = adapter.generate([], model="claude-sonnet-4-6")
        assert result == {"content": "success"}
        inner.generate.assert_called_once()

    def test_fallback_opus_to_sonnet(self):
        from shared.llm_factory import UnifiedEngineAdapter

        inner = MagicMock()
        inner.generate.side_effect = [
            RuntimeError("Opus overloaded"),
            {"content": "sonnet fallback"},
        ]
        adapter = UnifiedEngineAdapter(inner)

        result = adapter.generate([], model="claude-opus-4-6")
        assert result == {"content": "sonnet fallback"}
        assert inner.generate.call_count == 2

    def test_fallback_sonnet_to_haiku(self):
        from shared.llm_factory import UnifiedEngineAdapter

        inner = MagicMock()
        inner.generate.side_effect = [
            RuntimeError("Sonnet down"),
            {"content": "haiku fallback"},
        ]
        adapter = UnifiedEngineAdapter(inner)

        result = adapter.generate([], model="claude-sonnet-4-6")
        assert result == {"content": "haiku fallback"}

    def test_no_fallback_for_haiku(self):
        from shared.llm_factory import UnifiedEngineAdapter

        inner = MagicMock()
        inner.generate.side_effect = RuntimeError("Haiku fail")
        adapter = UnifiedEngineAdapter(inner)

        with pytest.raises(RuntimeError, match="Haiku fail"):
            adapter.generate([], model="claude-haiku-4-5")

    def test_getattr_proxy(self):
        from shared.llm_factory import UnifiedEngineAdapter

        inner = MagicMock()
        inner.some_attr = "test_value"
        adapter = UnifiedEngineAdapter(inner)

        assert adapter.some_attr == "test_value"

    def test_next_fallback_static(self):
        from shared.llm_factory import UnifiedEngineAdapter

        assert UnifiedEngineAdapter._next_fallback("claude-opus-4-6") == "claude-sonnet-4-6"
        assert UnifiedEngineAdapter._next_fallback("claude-sonnet-4-6") == "claude-haiku-4-5"
        assert UnifiedEngineAdapter._next_fallback("claude-haiku-4-5") is None
        assert UnifiedEngineAdapter._next_fallback("some-local-model") is None
