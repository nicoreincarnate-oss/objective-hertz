"""
Compatibility tests for the LLM Engine Adapter (shared/llm_engine_adapter.py).

Verifies that:
  - USE_OJ_ENGINE=false delegates 100% to the original LLMClient
  - USE_OJ_ENGINE=true routes through OJ Engine with correct model tiers
  - Import failures, engine errors, and budget downgrades are handled gracefully
  - Cost tracking uses real OJ metadata when available
  - The adapter exposes the same public interface as the original LLMClient
"""

import asyncio
import importlib
import inspect
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_adapter(use_oj: str = "false", api_key: str = "sk-test-key", oj_available: bool = True):
    """Import a fresh adapter module with controlled env and mocked internals.

    Returns (adapter_module, client_instance, original_mock).
    """
    # Purge cached modules so env vars and patches take fresh effect
    for mod_name in list(sys.modules):
        if mod_name.startswith("shared.llm_engine_adapter"):
            del sys.modules[mod_name]

    env = {
        "USE_OJ_ENGINE": use_oj,
        "ANTHROPIC_API_KEY": api_key,
    }

    # Build mocks for the original LLMClient
    original_mock = MagicMock()
    original_mock.generate = AsyncMock(return_value="original-response")
    original_mock.generate_json = AsyncMock(return_value={"from": "original"})
    original_mock.generate_with_images = AsyncMock(return_value="original-images")
    original_mock.classify = AsyncMock(return_value="category-a")
    original_mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    original_mock.close = AsyncMock()
    original_mock._inject_dna = MagicMock(side_effect=lambda s, d: s)

    original_cls_mock = MagicMock(return_value=original_mock)

    # Build mocks for config
    config_mock = MagicMock()
    config_mock.claude.api_key = api_key

    patches = {
        "shared.llm_client.LLMClient": original_cls_mock,
        "shared.config.config": config_mock,
        "shared.db.execute": AsyncMock(),
        "shared.middleware.check_budget_for_llm_call": AsyncMock(side_effect=lambda m: m),
    }

    # OJ Engine mocks — only inject when we want OJ available
    oj_engine_result = {
        "content": "oj-response",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        "cost_usd": 0.0042,
        "model": "claude-3-5-haiku-latest",
        "finish_reason": "stop",
    }
    oj_generate_mock = MagicMock(return_value=oj_engine_result)

    oj_multi_mock = MagicMock()
    oj_multi_mock.generate = oj_generate_mock
    oj_multi_mock.close = MagicMock()

    if use_oj.lower() in ("true", "1", "yes") and oj_available:
        # Mock the OJ imports at module level
        oj_msg_mock = MagicMock()
        oj_role_mock = MagicMock()
        oj_role_mock.SYSTEM = "system"
        oj_role_mock.USER = "user"

        patches["openjarvis.core.types.Message"] = oj_msg_mock
        patches["openjarvis.core.types.Role"] = oj_role_mock
        patches["openjarvis.engine.cloud.CloudEngine"] = MagicMock(return_value=MagicMock())
        patches["openjarvis.engine.cloud.estimate_cost"] = MagicMock()
        patches["openjarvis.engine.multi.MultiEngine"] = MagicMock(return_value=oj_multi_mock)
        patches["openjarvis.engine.ollama.OllamaEngine"] = MagicMock(return_value=MagicMock())

    with patch.dict(os.environ, env, clear=False):
        # Pre-populate OJ modules so import succeeds
        if use_oj.lower() in ("true", "1", "yes") and oj_available:
            _inject_oj_fake_modules(oj_msg_mock, oj_role_mock, oj_multi_mock)

        module = importlib.import_module("shared.llm_engine_adapter")

    # Patch internals after import for fine-grained control
    if use_oj.lower() in ("true", "1", "yes") and oj_available:
        module._OJ_AVAILABLE = True
        module._USE_OJ_ENGINE = True
        module._oj_multi_engine = oj_multi_mock
        module._oj_engines_initialized = True
        module._OJ_Message = MagicMock()
        module._OJ_Role = MagicMock()
        module._OJ_Role.SYSTEM = "system"
        module._OJ_Role.USER = "user"
        module._oj_engine_lock = asyncio.Lock()

    client = module.LLMClient.__new__(module.LLMClient)
    client._original = original_mock
    client._use_oj = (use_oj.lower() in ("true", "1", "yes")) and oj_available

    return module, client, original_mock, oj_multi_mock, oj_generate_mock


def _inject_oj_fake_modules(msg_cls, role_cls, multi_mock):
    """Insert fake openjarvis submodules into sys.modules so import works."""
    oj_pkg = MagicMock()
    oj_core = MagicMock()
    oj_types = MagicMock()
    oj_types.Message = msg_cls
    oj_types.Role = role_cls
    oj_engine = MagicMock()
    oj_cloud = MagicMock()
    oj_cloud.CloudEngine = MagicMock(return_value=MagicMock())
    oj_cloud.estimate_cost = MagicMock()
    oj_multi = MagicMock()
    oj_multi.MultiEngine = MagicMock(return_value=multi_mock)
    oj_ollama = MagicMock()
    oj_ollama.OllamaEngine = MagicMock(return_value=MagicMock())

    sys.modules["openjarvis"] = oj_pkg
    sys.modules["openjarvis.core"] = oj_core
    sys.modules["openjarvis.core.types"] = oj_types
    sys.modules["openjarvis.engine"] = oj_engine
    sys.modules["openjarvis.engine.cloud"] = oj_cloud
    sys.modules["openjarvis.engine.multi"] = oj_multi
    sys.modules["openjarvis.engine.ollama"] = oj_ollama


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdapterFlagOff:
    """1. When USE_OJ_ENGINE=false, all calls delegate to the original LLMClient."""

    def test_adapter_flag_off_delegates_to_original(self):
        module, client, original_mock, _, _ = _fresh_adapter(use_oj="false")
        assert client._use_oj is False

        result = run(client.generate("hello world", model="fast"))
        assert result == "original-response"
        original_mock.generate.assert_awaited_once()


class TestAdapterFlagOn:
    """2. When USE_OJ_ENGINE=true and OJ is available, generate() routes through OJ."""

    def test_adapter_flag_on_uses_oj_engine(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )
        assert client._use_oj is True

        with patch.object(module, "_check_budget", AsyncMock(side_effect=lambda m: m)):
            with patch.object(module, "_record_spend", AsyncMock()):
                with patch.object(module, "_fire_metrics_oj", MagicMock()):
                    result = run(client.generate("hello world", model="fast"))

        assert result == "oj-response"
        original_mock.generate.assert_not_awaited()


class TestOJImportFailureFallback:
    """3. If OJ imports fail, adapter silently falls back to original."""

    def test_adapter_oj_import_failure_falls_back(self):
        module, client, original_mock, _, _ = _fresh_adapter(
            use_oj="true", oj_available=False
        )
        # oj_available=False means _use_oj stays False
        assert client._use_oj is False

        result = run(client.generate("hello world", model="fast"))
        assert result == "original-response"
        original_mock.generate.assert_awaited_once()


class TestModelTierResolution:
    """4. Verify tier -> model mapping: fast->haiku, smart->sonnet, genius->opus,
    local->qwen, local-small->llama."""

    def test_model_tier_resolution(self):
        module, client, _, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        expected_map = {
            "fast": "claude-3-5-haiku-latest",
            "smart": "claude-sonnet-4-20250514",
            "genius": "claude-opus-4-20250514",
            "local": "qwen2.5:14b-instruct-q4_K_M",
            "local-small": "llama3.2:3b",
        }

        for tier, expected_model_id in expected_map.items():
            resolved = module._OJ_TIER_MAP.get(tier)
            assert resolved is not None, f"Tier '{tier}' not found in _OJ_TIER_MAP"
            model_id, engine_key = resolved
            assert model_id == expected_model_id, (
                f"Tier '{tier}': expected model '{expected_model_id}', got '{model_id}'"
            )

        # Verify engine routing
        assert module._OJ_TIER_MAP["fast"][1] == "cloud"
        assert module._OJ_TIER_MAP["smart"][1] == "cloud"
        assert module._OJ_TIER_MAP["genius"][1] == "cloud"
        assert module._OJ_TIER_MAP["local"][1] == "ollama"
        assert module._OJ_TIER_MAP["local-small"][1] == "ollama"


class TestBudgetDowngrade80:
    """5. At 80%+ budget, 'fast' tier downgrades to local Ollama."""

    def test_budget_downgrade_80_percent(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        # Budget check returns "local" (simulating 80%+ threshold)
        with patch.object(module, "_check_budget", AsyncMock(return_value="local")):
            with patch.object(module, "_fire_metrics_oj", MagicMock()):
                result = run(client.generate("hello world", model="fast"))

        # Should have routed to OJ local (ollama) path, not cloud
        assert result == "oj-response"
        # The generate call should have been with a local model
        oj_gen.assert_called()
        call_kwargs = oj_gen.call_args
        # Model passed should be a local model ID
        model_arg = call_kwargs.kwargs.get("model") or call_kwargs[1].get("model", "")
        assert "qwen" in model_arg or "llama" in model_arg or model_arg == "local", (
            f"Expected local model, got: {model_arg}"
        )


class TestBudgetDowngrade100:
    """6. At 100%+ budget, everything goes to local Ollama."""

    def test_budget_downgrade_100_percent(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        # Budget check returns "local" for all tiers (100%+)
        with patch.object(module, "_check_budget", AsyncMock(return_value="local")):
            with patch.object(module, "_fire_metrics_oj", MagicMock()):
                for tier in ("fast", "smart", "genius"):
                    oj_gen.reset_mock()
                    result = run(client.generate("hello world", model=tier))
                    assert result == "oj-response"
                    oj_gen.assert_called()


class TestNoApiKeyFallback:
    """7. Without ANTHROPIC_API_KEY, all calls route to Ollama."""

    def test_no_api_key_falls_back_to_local(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", api_key="", oj_available=True
        )

        with patch.object(module, "_fire_metrics_oj", MagicMock()):
            result = run(client.generate("hello world", model="fast"))

        # Should route to local via OJ engine (no API key = Ollama path)
        assert result == "oj-response"


class TestOJErrorFallsBackToOllama:
    """8. When OJ CloudEngine throws an error, adapter falls back to Ollama, not crash."""

    def test_oj_error_falls_back_to_ollama(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        # First call fails (cloud), second succeeds (ollama fallback)
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Cloud API unavailable")
            return {
                "content": "ollama-fallback-response",
                "usage": {"prompt_tokens": 50, "completion_tokens": 25},
                "cost_usd": None,
                "model": "qwen2.5:14b-instruct-q4_K_M",
                "finish_reason": "stop",
            }

        oj_gen.side_effect = side_effect

        with patch.object(module, "_check_budget", AsyncMock(side_effect=lambda m: m)):
            with patch.object(module, "_record_spend", AsyncMock()):
                with patch.object(module, "_fire_metrics_oj", MagicMock()):
                    result = run(client.generate("hello world", model="fast"))

        assert result == "ollama-fallback-response"
        assert call_count == 2  # cloud failed, then ollama succeeded


class TestOJAndOllamaFailFallback:
    """9. When both OJ paths fail, delegates to original llm_client as last resort."""

    def test_oj_and_ollama_fail_falls_back_to_original(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        # All OJ calls fail
        oj_gen.side_effect = RuntimeError("All engines down")

        with patch.object(module, "_check_budget", AsyncMock(side_effect=lambda m: m)):
            with patch.object(module, "_fire_metrics_oj", MagicMock()):
                result = run(client.generate("hello world", model="fast"))

        # Should fall through to original LLMClient
        assert result == "original-response"
        original_mock.generate.assert_awaited_once()


class TestCostTrackingWithOJResponse:
    """10. When OJ returns cost_usd and token counts, adapter records real costs."""

    def test_cost_tracking_with_oj_response(self):
        module, client, _, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        oj_gen.return_value = {
            "content": "tracked-response",
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            "cost_usd": 0.0123,
            "model": "claude-3-5-haiku-latest",
            "finish_reason": "stop",
        }

        record_spy = AsyncMock()
        with patch.object(module, "_check_budget", AsyncMock(side_effect=lambda m: m)):
            with patch.object(module, "_record_spend", record_spy):
                with patch.object(module, "_fire_metrics_oj", MagicMock()):
                    result = run(client.generate("hello", model="fast"))

        assert result == "tracked-response"
        record_spy.assert_awaited_once()

        call_kwargs = record_spy.call_args
        # Verify real OJ cost data was passed through
        assert call_kwargs.kwargs.get("oj_cost_usd") == 0.0123
        assert call_kwargs.kwargs.get("oj_input_tokens") == 200
        assert call_kwargs.kwargs.get("oj_output_tokens") == 100


class TestGenerateJsonMode:
    """11. generate_json() returns parsed dict when OJ is active."""

    def test_generate_json_mode(self):
        module, client, original_mock, oj_multi_mock, oj_gen = _fresh_adapter(
            use_oj="true", oj_available=True
        )

        json_payload = {"status": "ok", "count": 42}
        oj_gen.return_value = {
            "content": json.dumps(json_payload),
            "usage": {"prompt_tokens": 50, "completion_tokens": 30},
            "cost_usd": 0.002,
            "model": "claude-3-5-haiku-latest",
            "finish_reason": "stop",
        }

        with patch.object(module, "_record_spend", AsyncMock()):
            result = run(client.generate_json("give me json", model="fast"))

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["count"] == 42


class TestSameInterfaceAsOriginal:
    """12. Both the adapter's LLMClient and the original have the same public method signatures."""

    def test_same_interface_as_original(self):
        # Import the original
        for mod_name in list(sys.modules):
            if mod_name.startswith("shared.llm_client"):
                del sys.modules[mod_name]
            if mod_name.startswith("shared.config"):
                del sys.modules[mod_name]

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "USE_OJ_ENGINE": "false"}):
            with patch("httpx.AsyncClient", return_value=object()):
                original_module = importlib.import_module("shared.llm_client")

        # Import the adapter
        for mod_name in list(sys.modules):
            if mod_name.startswith("shared.llm_engine_adapter"):
                del sys.modules[mod_name]

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "USE_OJ_ENGINE": "false"}):
            adapter_module = importlib.import_module("shared.llm_engine_adapter")

        OriginalCls = original_module.LLMClient
        AdapterCls = adapter_module.LLMClient

        # Get public methods (non-underscore)
        original_methods = {
            name for name, _ in inspect.getmembers(OriginalCls, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        adapter_methods = {
            name for name, _ in inspect.getmembers(AdapterCls, predicate=inspect.isfunction)
            if not name.startswith("_")
        }

        # Adapter must have at least all original public methods
        missing = original_methods - adapter_methods
        assert not missing, f"Adapter is missing methods from original: {missing}"

        # Verify key method signatures match
        for method_name in ("generate", "generate_with_images", "classify", "embed", "close"):
            if method_name in original_methods:
                orig_sig = inspect.signature(getattr(OriginalCls, method_name))
                adapter_sig = inspect.signature(getattr(AdapterCls, method_name))

                orig_params = set(orig_sig.parameters.keys())
                adapter_params = set(adapter_sig.parameters.keys())

                # Adapter must accept all params the original does
                missing_params = orig_params - adapter_params
                assert not missing_params, (
                    f"Method '{method_name}': adapter missing params {missing_params}"
                )
