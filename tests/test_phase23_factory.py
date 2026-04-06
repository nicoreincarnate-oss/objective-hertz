"""Tests for Phase 23 Tasks 23-06, 23-14, 23-15: UnifiedLLMFactory + Withholding.

Validates:
- Factory lazy provider initialization
- Fallback chain execution through factory
- WithholdingBuffer error classification (fatal vs recoverable)
- WithholdingBuffer recovery with fallback
- Shadow comparator basic behavior
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies
# ---------------------------------------------------------------------------

if "httpx" not in sys.modules:
    _fake_httpx = types.ModuleType("httpx")
    _fake_httpx.AsyncClient = MagicMock
    _fake_httpx.Timeout = MagicMock
    _fake_httpx.HTTPError = type("HTTPError", (Exception,), {})
    sys.modules["httpx"] = _fake_httpx

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

_fake_db = types.ModuleType("shared.db")
for attr in ("execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool",
             "insert_task", "transaction", "emit_event"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.get_config = AsyncMock(return_value=None)
sys.modules["shared.db"] = _fake_db

_fake_config_mod = types.ModuleType("shared.config")
_fake_config_mod.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test-factory"),
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

_fake_prompt_builder = types.ModuleType("shared.prompt_builder")
_fake_prompt_builder.CACHE_BOUNDARY_MARKER = "---CACHE_BOUNDARY---"
_latch_mock = MagicMock()
_latch_mock.peek = MagicMock(return_value=None)
_latch_mock.get = MagicMock(side_effect=lambda key, factory: factory())
_fake_prompt_builder.get_session_latch = MagicMock(return_value=_latch_mock)
sys.modules["shared.prompt_builder"] = _fake_prompt_builder

_fake_middleware = types.ModuleType("shared.middleware")
_fake_middleware.check_budget_for_llm_call = AsyncMock(side_effect=lambda m: m)
sys.modules["shared.middleware"] = _fake_middleware

_fake_obs = types.ModuleType("shared.observability")
_fake_obs.record_llm_call = AsyncMock()
_fake_obs._task_id = MagicMock()
_fake_obs._task_id.get = MagicMock(return_value=None)
sys.modules["shared.observability"] = _fake_obs

_fake_cost_events = types.ModuleType("shared.cost_events")
_fake_cost_events.CostEvent = MagicMock
_fake_cost_events.emit_cost_event = AsyncMock()
sys.modules["shared.cost_events"] = _fake_cost_events

_fake_dna = types.ModuleType("shared.agent_dna")
_fake_dna.get_dna = MagicMock(return_value=None)
_fake_dna.get_circuit_breaker = MagicMock(return_value=MagicMock(record=MagicMock()))
sys.modules["shared.agent_dna"] = _fake_dna

_fake_airllm_policy = types.ModuleType("shared.airllm_policy")
_fake_airllm_policy.should_route_to_heavy_local = MagicMock(return_value=False)
_fake_airllm_policy.choose_heavy_local_backend = MagicMock(return_value="ollama")
_fake_airllm_policy.explain_heavy_local_routing = MagicMock(return_value="test")
sys.modules["shared.airllm_policy"] = _fake_airllm_policy

_fake_learning = types.ModuleType("shared.learning_extractor")
_fake_learning.maybe_extract_background = MagicMock()
sys.modules["shared.learning_extractor"] = _fake_learning

_fake_memory_index = types.ModuleType("shared.memory_index")
_fake_memory_index.get_memory_index = MagicMock(return_value="")
_fake_memory_index._MAX_ENTRIES = 100
sys.modules["shared.memory_index"] = _fake_memory_index

from shared.llm_unified import (
    DEFAULT_CHAINS,
    FallbackChain,
    FallbackStep,
    LLMResponse,
    ModelTier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_response(content="test response", tier=ModelTier.SMART, model_id="claude-sonnet-4-6"):
    return LLMResponse(
        content=content,
        model_id=model_id,
        engine="anthropic",
        tier_requested=tier,
        tier_actual=tier,
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        latency_ms=200,
    )


# ---------------------------------------------------------------------------
# WithholdingBuffer Tests
# ---------------------------------------------------------------------------

class TestWithholdingBuffer:
    """Test WithholdingBuffer error classification and recovery."""

    def test_fatal_errors_not_withheld(self):
        """Fatal errors (auth, invalid_request) raise immediately."""
        from shared.llm_withholding import WithholdingBuffer

        assert WithholdingBuffer._is_fatal(
            RuntimeError("authentication failed")
        ) is True
        assert WithholdingBuffer._is_fatal(
            RuntimeError("invalid_api_key")
        ) is True
        assert WithholdingBuffer._is_fatal(
            RuntimeError("unauthorized access")
        ) is True
        assert WithholdingBuffer._is_fatal(
            RuntimeError("permission denied")
        ) is True

    def test_recoverable_errors_identified(self):
        """Recoverable errors (rate limit, timeout, 5xx) are identified."""
        from shared.llm_withholding import WithholdingBuffer

        assert WithholdingBuffer._is_recoverable(
            RuntimeError("rate_limit exceeded")
        ) is True
        assert WithholdingBuffer._is_recoverable(
            RuntimeError("429 Too Many Requests")
        ) is True
        assert WithholdingBuffer._is_recoverable(
            RuntimeError("request timed out")
        ) is True
        assert WithholdingBuffer._is_recoverable(
            RuntimeError("502 Bad Gateway")
        ) is True
        assert WithholdingBuffer._is_recoverable(
            RuntimeError("service_unavailable")
        ) is True

    def test_non_fatal_non_recoverable(self):
        """Some errors are neither clearly fatal nor clearly recoverable."""
        from shared.llm_withholding import WithholdingBuffer

        err = RuntimeError("some random error")
        assert WithholdingBuffer._is_fatal(err) is False
        assert WithholdingBuffer._is_recoverable(err) is False

    def test_withholding_recovers_at_depth_1(self):
        """When first provider fails recoverably, second succeeds."""
        from shared.llm_withholding import WithholdingBuffer

        mock_factory = MagicMock()
        mock_provider_1 = MagicMock()
        mock_provider_2 = MagicMock()

        mock_provider_1.generate = AsyncMock(
            side_effect=RuntimeError("rate_limit")
        )
        expected_response = _make_response("recovered!")
        mock_provider_2.generate = AsyncMock(return_value=expected_response)

        mock_factory._providers = {
            "anthropic": mock_provider_1,
            "ollama": mock_provider_2,
        }
        mock_factory._resolve_model_id = AsyncMock(return_value="qwen2.5:14b")

        chain = FallbackChain([
            FallbackStep(ModelTier.SMART, "claude-sonnet-4-6", "anthropic", 5.0),
            FallbackStep(ModelTier.LOCAL, "", "ollama", 5.0),
        ])

        buffer = WithholdingBuffer(mock_factory)
        result = _run(
            buffer.execute_chain_with_withholding(
                chain, "test prompt", "", 2048, 0.7
            )
        )

        assert result.content == "recovered!"
        assert result.fallback_triggered is True
        assert result.fallback_depth == 1

    def test_withholding_raises_on_fatal(self):
        """Fatal errors are NOT withheld -- they raise immediately."""
        from shared.llm_withholding import WithholdingBuffer

        mock_factory = MagicMock()
        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(
            side_effect=RuntimeError("authentication failed")
        )
        mock_factory._providers = {"anthropic": mock_provider}

        chain = FallbackChain([
            FallbackStep(ModelTier.SMART, "claude-sonnet-4-6", "anthropic", 5.0),
            FallbackStep(ModelTier.LOCAL, "", "ollama", 5.0),
        ])

        buffer = WithholdingBuffer(mock_factory)
        with pytest.raises(RuntimeError, match="authentication"):
            _run(
                buffer.execute_chain_with_withholding(
                    chain, "test", "", 2048, 0.7
                )
            )

    def test_all_steps_exhausted_raises(self):
        """When all steps fail recoverably, a RuntimeError is raised."""
        from shared.llm_withholding import WithholdingBuffer

        mock_factory = MagicMock()
        mock_provider = MagicMock()
        mock_provider.generate = AsyncMock(
            side_effect=RuntimeError("503 service unavailable")
        )
        mock_factory._providers = {"anthropic": mock_provider, "ollama": mock_provider}
        mock_factory._resolve_model_id = AsyncMock(return_value="test-model")

        chain = FallbackChain([
            FallbackStep(ModelTier.SMART, "claude-sonnet-4-6", "anthropic", 5.0),
            FallbackStep(ModelTier.LOCAL, "", "ollama", 5.0),
        ])

        buffer = WithholdingBuffer(mock_factory)
        with pytest.raises(RuntimeError, match="recovery paths exhausted"):
            _run(
                buffer.execute_chain_with_withholding(
                    chain, "test", "", 2048, 0.7
                )
            )


# ---------------------------------------------------------------------------
# ShadowComparator Tests
# ---------------------------------------------------------------------------

class TestShadowComparator:
    """Test ShadowComparator basic behavior."""

    def test_stats_initial(self):
        from shared.llm_shadow import ShadowComparator

        comp = ShadowComparator()
        stats = comp.stats
        assert stats["comparison_count"] == 0
        assert stats["error_delta_count"] == 0
        assert stats["ready_for_cutover"] is False

    def test_not_ready_under_100_comparisons(self):
        from shared.llm_shadow import ShadowComparator

        comp = ShadowComparator()
        comp._comparison_count = 50
        assert comp.is_ready_for_cutover() is False

    def test_ready_after_sufficient_comparisons(self):
        from shared.llm_shadow import ShadowComparator

        comp = ShadowComparator()
        comp._comparison_count = 200
        comp._error_delta_count = 0
        comp._latency_deltas = [10.0] * 200
        assert comp.is_ready_for_cutover() is True

    def test_not_ready_high_error_rate(self):
        from shared.llm_shadow import ShadowComparator

        comp = ShadowComparator()
        comp._comparison_count = 200
        comp._error_delta_count = 5  # 2.5% > 1%
        assert comp.is_ready_for_cutover() is False

    def test_not_ready_high_latency(self):
        from shared.llm_shadow import ShadowComparator

        comp = ShadowComparator()
        comp._comparison_count = 200
        comp._error_delta_count = 0
        comp._latency_deltas = [300.0] * 200  # P95 = 300ms > 200ms
        assert comp.is_ready_for_cutover() is False


# ---------------------------------------------------------------------------
# Factory Provider Init Tests
# ---------------------------------------------------------------------------

class TestFactoryProviderInit:
    """Test that UnifiedLLMFactory initializes providers correctly."""

    def test_factory_starts_empty(self):
        from shared.llm_factory import UnifiedLLMFactory as ProviderFactory

        factory = ProviderFactory()
        assert factory._providers == {}
        assert factory._providers_initialized is False

    def test_factory_initializes_on_ensure(self):
        from shared.llm_factory import UnifiedLLMFactory as ProviderFactory

        factory = ProviderFactory()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            factory._ensure_providers()
        assert factory._providers_initialized is True
        # Should have at least ollama provider
        assert "ollama" in factory._providers

    def test_factory_withholding_default_enabled(self):
        from shared.llm_factory import UnifiedLLMFactory as ProviderFactory

        factory = ProviderFactory()
        assert factory._withholding_enabled is True

    def test_factory_withholding_can_be_disabled(self):
        from shared.llm_factory import UnifiedLLMFactory as ProviderFactory

        with patch.dict("os.environ", {"UNIFIED_LLM_WITHHOLDING": "false"}):
            factory = ProviderFactory()
        assert factory._withholding_enabled is False
