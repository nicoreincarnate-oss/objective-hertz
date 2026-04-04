"""Regression tests for Claude-Max-first LLM routing.

Budget enforcement is via shared.middleware.check_budget_for_llm_call.
"""

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


def set_airllm_enabled(module, value: bool):
    object.__setattr__(module.config.airllm, "enabled", value)


def make_client(api_key="sk-test-key"):
    """Build a test LLMClient with mocked internals.

    Sets ANTHROPIC_API_KEY in env before importing so the frozen config
    dataclass picks it up and generate() doesn't short-circuit to Ollama.
    """
    sys.modules.pop("shared.llm_client", None)
    sys.modules.pop("shared.config", None)
    sys.modules.pop("shared.airllm_policy", None)

    env = {"ANTHROPIC_API_KEY": api_key}
    with patch.dict(os.environ, env, clear=False):
        with patch("httpx.AsyncClient", return_value=object()):
            module = importlib.import_module("shared.llm_client")

    LLMClient = module.LLMClient
    client = object.__new__(LLMClient)
    client._http = None
    client._last_usage = None
    client._airllm_model = None
    client._airllm_model_id = None
    client._airllm_lock = asyncio.Lock()
    client._claude_generate = AsyncMock(return_value="claude")
    client._ollama_generate = AsyncMock(return_value="local")
    client._airllm_generate = AsyncMock(return_value="airllm")
    client._ollm_generate = AsyncMock(return_value="ollm")
    client._record_claude_spend = AsyncMock()
    return client


def test_generate_fast_uses_claude_haiku():
    """'fast' is Claude Haiku — the primary workhorse via Claude Max subscription."""
    client = make_client()
    with patch("shared.middleware.check_budget_for_llm_call", AsyncMock(side_effect=lambda m: m)):
        result = run(client.generate("hello", model="fast"))
    assert result == "claude"
    client._claude_generate.assert_awaited_once()
    client._record_claude_spend.assert_awaited_once()


def test_generate_smart_uses_claude_sonnet():
    """'smart' is Claude Sonnet — best quality for proposals, strategy."""
    client = make_client()
    with patch("shared.middleware.check_budget_for_llm_call", AsyncMock(side_effect=lambda m: m)):
        result = run(client.generate("hello", model="smart"))
    assert result == "claude"
    client._claude_generate.assert_awaited_once()
    client._record_claude_spend.assert_awaited_once()


def test_generate_local_uses_ollama():
    """'local' goes directly to Ollama — no Claude, no budget check."""
    client = make_client()
    result = run(client.generate("hello", model="local"))
    assert result == "local"
    client._ollama_generate.assert_awaited_once()
    client._claude_generate.assert_not_awaited()


def test_generate_local_heavy_prefers_airllm():
    """Heavy local tier should route to AirLLM when it is explicitly requested."""
    client = make_client()
    import shared.llm_client as module

    old = module.config.airllm.enabled
    old_ollm = module.config.ollm.enabled
    try:
        set_airllm_enabled(module, True)
        object.__setattr__(module.config.ollm, "enabled", False)
        result = run(client.generate("hello", model="local-heavy"))
    finally:
        set_airllm_enabled(module, old)
        object.__setattr__(module.config.ollm, "enabled", old_ollm)
    assert result == "airllm"
    client._airllm_generate.assert_awaited_once()
    client._ollama_generate.assert_not_awaited()


def test_generate_local_research_stage_uses_airllm_when_enabled():
    """Heavy research stages should use AirLLM as the best local backend."""
    client = make_client(api_key="")
    import shared.llm_client as module

    old = module.config.airllm.enabled
    old_ollm = module.config.ollm.enabled
    try:
        set_airllm_enabled(module, True)
        object.__setattr__(module.config.ollm, "enabled", False)
        result = run(client.generate("Summarize these papers", model="local", pipeline_stage="research:papers"))
    finally:
        set_airllm_enabled(module, old)
        object.__setattr__(module.config.ollm, "enabled", old_ollm)
    assert result == "airllm"
    client._airllm_generate.assert_awaited_once()


def test_generate_airllm_falls_back_to_ollama_if_unavailable():
    """AirLLM is optional; local heavy work must still complete via Ollama."""
    client = make_client(api_key="")
    client._airllm_generate = AsyncMock(side_effect=RuntimeError("missing airllm"))
    import shared.llm_client as module

    old = module.config.airllm.enabled
    old_ollm = module.config.ollm.enabled
    try:
        set_airllm_enabled(module, True)
        object.__setattr__(module.config.ollm, "enabled", False)
        result = run(client.generate("Summarize these papers", model="local-heavy", pipeline_stage="research:papers"))
    finally:
        set_airllm_enabled(module, old)
        object.__setattr__(module.config.ollm, "enabled", old_ollm)
    assert result == "local"
    client._ollama_generate.assert_awaited_once()


def test_generate_huge_context_local_prefers_ollm():
    client = make_client(api_key="")
    import shared.llm_client as module

    old_airllm = module.config.airllm.enabled
    old_ollm = module.config.ollm.enabled
    try:
        set_airllm_enabled(module, True)
        object.__setattr__(module.config.ollm, "enabled", True)
        result = run(client.generate("digest", model="huge-context-local"))
    finally:
        set_airllm_enabled(module, old_airllm)
        object.__setattr__(module.config.ollm, "enabled", old_ollm)
    assert result == "ollm"
    client._ollm_generate.assert_awaited_once()


def test_classify_uses_local_small_model():
    """Classification is simple enough for the smallest local model."""
    client = make_client()
    client.generate = AsyncMock(return_value="interested")
    result = run(client.classify("hello", ["interested", "lost"]))
    assert result == "interested"
    assert client.generate.await_args.kwargs["model"] == "local-small"


def test_budget_check_downgrades_fast_to_ollama():
    """When check_budget_for_llm_call returns 'local', fast calls fall back to Ollama."""
    client = make_client()
    with patch("shared.middleware.check_budget_for_llm_call", AsyncMock(return_value="local")):
        result = run(client.generate("hello", model="fast"))
    assert result == "local"
    client._ollama_generate.assert_awaited_once()
    client._claude_generate.assert_not_awaited()


def test_no_api_key_falls_back_to_ollama():
    """Without ANTHROPIC_API_KEY, everything goes to Ollama."""
    client = make_client(api_key="")
    result = run(client.generate("hello", model="fast"))
    assert result == "local"
    client._ollama_generate.assert_awaited_once()
