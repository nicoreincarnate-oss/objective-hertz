"""
Tests for Phase 2: model_selector integration into llm_engine_adapter.

Verifies that:
  - The adapter calls select_model() for tier resolution when available
  - Import failure falls back to hardcoded _OJ_TIER_MAP
  - pipeline_stage is passed as task_type
  - Fallback model from selection is used on primary failure
  - Selection reason is logged
"""

import asyncio
import importlib
import logging
import os
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake ModelSelection matching the real dataclass shape
# ---------------------------------------------------------------------------


@dataclass
class FakeModelSelection:
    model_id: str = "claude-3-5-haiku-latest"
    engine: str = "cloud"
    tier: str = "fast"
    reason: str = "catalog: claude-3-5-haiku (Anthropic)"
    estimated_cost: float = 0.0035
    context_length: int = 200000
    fallback_model: str = "llama3.2:3b"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_oj_result(content="oj-response"):
    """Build a fake OJ Engine.generate() return dict."""
    return {
        "content": content,
        "usage": {"prompt_tokens": 100, "input_tokens": 100, "output_tokens": 50, "completion_tokens": 50},
        "cost_usd": 0.002,
        "model": "claude-3-5-haiku-latest",
        "finish_reason": "stop",
    }


def _fresh_adapter_with_selector(
    selector_available: bool = True,
    selector_side_effect=None,
    api_key: str = "sk-test",
):
    """Import a fresh adapter module with model_selector mocked.

    Returns (adapter_module, select_model_mock, engine_mock, original_mock).
    """
    # Purge cached adapter module
    for mod_name in list(sys.modules):
        if "llm_engine_adapter" in mod_name:
            del sys.modules[mod_name]

    # Build select_model mock
    select_model_mock = AsyncMock()
    if selector_side_effect:
        select_model_mock.side_effect = selector_side_effect
    else:
        select_model_mock.return_value = FakeModelSelection()

    # OJ Engine mocks
    engine_mock = MagicMock()
    engine_mock.generate = MagicMock(return_value=_make_oj_result())
    cloud_eng = MagicMock()
    ollama_eng = MagicMock()
    multi_eng = MagicMock(return_value=engine_mock)

    # OJ type mocks
    msg_cls = MagicMock()
    role_cls = MagicMock()
    role_cls.SYSTEM = "system"
    role_cls.USER = "user"

    # Original LLMClient mock
    original_mock = MagicMock()
    original_mock.generate = AsyncMock(return_value="original-response")
    original_mock._inject_dna = MagicMock(side_effect=lambda s, d: s)
    original_mock.close = AsyncMock()
    original_cls = MagicMock(return_value=original_mock)

    # Config mock
    config_mock = MagicMock()
    config_mock.claude.api_key = api_key

    env = {"USE_OJ_ENGINE": "true"}

    oj_modules = {
        "openjarvis": MagicMock(),
        "openjarvis.core": MagicMock(),
        "openjarvis.core.types": MagicMock(Message=msg_cls, Role=role_cls),
        "openjarvis.engine": MagicMock(),
        "openjarvis.engine.cloud": MagicMock(CloudEngine=MagicMock(return_value=cloud_eng), estimate_cost=MagicMock()),
        "openjarvis.engine.multi": MagicMock(MultiEngine=MagicMock(return_value=engine_mock)),
        "openjarvis.engine.ollama": MagicMock(OllamaEngine=MagicMock(return_value=ollama_eng)),
        "openjarvis.engine._stubs": MagicMock(ResponseFormat=MagicMock()),
    }

    # Mock model_selector import
    if selector_available:
        selector_module = MagicMock()
        selector_module.select_model = select_model_mock
        oj_modules["shared.model_selector"] = selector_module
    # If not available, we don't add it — import will fail

    patches = {
        "shared.llm_client.LLMClient": original_cls,
        "shared.config.config": config_mock,
        "shared.middleware.check_budget_for_llm_call": AsyncMock(side_effect=lambda t: t),
    }

    with patch.dict(os.environ, env), \
         patch.dict(sys.modules, oj_modules), \
         patch.multiple("shared.llm_client", LLMClient=original_cls, create=True), \
         patch("shared.config.config", config_mock):

        # Manually set the module-level selector references before importing
        # We need to control whether the import succeeds
        if selector_available:
            # Pre-seed so the try/except in the adapter finds it
            sys.modules["shared.model_selector"] = oj_modules["shared.model_selector"]
        else:
            # Make sure it's NOT in sys.modules so the import fails
            sys.modules.pop("shared.model_selector", None)

        mod = importlib.import_module("shared.llm_engine_adapter")

        # Patch internals that were resolved at import time
        mod._OJ_AVAILABLE = True
        mod._OJ_Message = msg_cls
        mod._OJ_Role = role_cls
        mod._oj_multi_engine = engine_mock
        mod._oj_engines_initialized = True

        if selector_available:
            mod._MODEL_SELECTOR_AVAILABLE = True
            mod._select_model_fn = select_model_mock
        else:
            mod._MODEL_SELECTOR_AVAILABLE = False
            mod._select_model_fn = None

        # Suppress metrics / spend recording
        mod._fire_metrics_oj = MagicMock()
        mod._record_spend = AsyncMock()

        client = mod.LLMClient()
        client._use_oj = True
        # Patch the original inside the client
        client._original = original_mock

        return mod, select_model_mock, engine_mock, original_mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAdapterUsesModelSelector:
    """Test that the adapter calls select_model for tier resolution."""

    def test_adapter_uses_model_selector(self):
        """When model_selector is available, _oj_generate calls select_model."""
        mod, sel_mock, engine_mock, _ = _fresh_adapter_with_selector(selector_available=True)

        sel_mock.return_value = FakeModelSelection(
            model_id="claude-3-5-haiku-latest",
            engine="cloud",
            reason="catalog: haiku (Anthropic)",
        )

        async def _run():
            content, meta = await mod._oj_generate(
                "hello",
                model="fast",
                pipeline_stage="email",
            )
            return content, meta

        content, meta = run(_run())

        # select_model was called with the right args
        sel_mock.assert_called_once()
        call_kwargs = sel_mock.call_args
        assert call_kwargs[1]["tier"] == "fast" or call_kwargs[0][0] == "fast"
        # Engine.generate was called with the selector's model_id
        engine_mock.generate.assert_called_once()
        gen_kwargs = engine_mock.generate.call_args
        assert gen_kwargs[1]["model"] == "claude-3-5-haiku-latest"

    def test_adapter_selector_unavailable_uses_hardcoded(self):
        """When model_selector import fails, adapter falls back to _OJ_TIER_MAP."""
        mod, sel_mock, engine_mock, _ = _fresh_adapter_with_selector(selector_available=False)

        async def _run():
            content, meta = await mod._oj_generate(
                "hello",
                model="smart",
            )
            return content, meta

        content, meta = run(_run())

        # select_model was NOT called
        sel_mock.assert_not_called()
        # Engine.generate was called with the hardcoded model for "smart"
        engine_mock.generate.assert_called_once()
        gen_kwargs = engine_mock.generate.call_args
        assert gen_kwargs[1]["model"] == "claude-sonnet-4-20250514"

    def test_adapter_passes_pipeline_stage_as_task_type(self):
        """pipeline_stage is forwarded as task_type to select_model."""
        mod, sel_mock, engine_mock, _ = _fresh_adapter_with_selector(selector_available=True)

        async def _run():
            await mod._oj_generate(
                "research this",
                model="smart",
                pipeline_stage="lead_research",
            )

        run(_run())

        sel_mock.assert_called_once()
        kwargs = sel_mock.call_args[1] if sel_mock.call_args[1] else {}
        # task_type should be the pipeline_stage value
        assert kwargs.get("task_type") == "lead_research"

    def test_adapter_uses_fallback_model_on_error(self):
        """When primary generate fails, adapter uses selector's fallback_model."""
        call_count = [0]

        def engine_generate_side_effect(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Cloud API down")
            return _make_oj_result("fallback-response")

        mod, sel_mock, engine_mock, _ = _fresh_adapter_with_selector(selector_available=True)
        engine_mock.generate = MagicMock(side_effect=engine_generate_side_effect)

        sel_mock.return_value = FakeModelSelection(
            model_id="claude-sonnet-4-20250514",
            engine="cloud",
            tier="smart",
            fallback_model="qwen3:8b",
        )

        async def _run():
            content, meta = await mod._oj_generate_with_ollama_fallback(
                "test prompt",
                model="smart",
                pipeline_stage="research",
            )
            return content, meta

        content, meta = run(_run())

        # Should have called generate twice: primary failed, fallback succeeded
        assert engine_mock.generate.call_count == 2
        assert content == "fallback-response"

    def test_adapter_logs_selection_reason(self, caplog):
        """Selection reason from model_selector is logged."""
        mod, sel_mock, engine_mock, _ = _fresh_adapter_with_selector(selector_available=True)

        sel_mock.return_value = FakeModelSelection(
            model_id="claude-3-5-haiku-latest",
            engine="cloud",
            reason="catalog: claude-3-5-haiku (Anthropic); task=email",
        )

        async def _run():
            with caplog.at_level(logging.INFO, logger="perseus.llm.adapter"):
                content, meta = await mod._oj_generate(
                    "write email",
                    model="fast",
                    pipeline_stage="email",
                )
                return content, meta

        content, meta = run(_run())

        # The reason should appear in logs
        assert any("catalog: claude-3-5-haiku" in r.message for r in caplog.records)
        # The reason should also be in metadata
        assert meta.get("selector_reason") == "catalog: claude-3-5-haiku (Anthropic); task=email"
        assert meta.get("selector_estimated_cost") == 0.0035
