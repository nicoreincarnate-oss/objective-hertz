"""Regression tests for Claude-Max-first LLM routing."""

import asyncio
import importlib
import os
import sys
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


def make_client(api_key="sk-test-key"):
    """Build a test LLMClient with mocked internals.

    Sets ANTHROPIC_API_KEY in env before importing so the frozen config
    dataclass picks it up and generate() doesn't short-circuit to Ollama.
    """
    sys.modules.pop("shared.llm_client", None)
    sys.modules.pop("shared.config", None)

    env = {"ANTHROPIC_API_KEY": api_key}
    with patch.dict(os.environ, env, clear=False):
        with patch("httpx.AsyncClient", return_value=object()):
            module = importlib.import_module("shared.llm_client")

    LLMClient = module.LLMClient
    client = object.__new__(LLMClient)
    client._http = None
    client._budget_gate = AsyncMock(side_effect=lambda model: model)
    client._claude_generate = AsyncMock(return_value="claude")
    client._ollama_generate = AsyncMock(return_value="local")
    client._record_claude_spend = AsyncMock()
    return client


def test_generate_fast_uses_claude_haiku():
    """'fast' is Claude Haiku — the primary workhorse via Claude Max subscription."""
    client = make_client()
    result = run(client.generate("hello", model="fast"))
    assert result == "claude"
    client._claude_generate.assert_awaited_once()
    client._record_claude_spend.assert_awaited_once()


def test_generate_smart_uses_claude_sonnet():
    """'smart' is Claude Sonnet — best quality for proposals, strategy."""
    client = make_client()
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


def test_classify_uses_local_small_model():
    """Classification is simple enough for the smallest local model."""
    client = make_client()
    client.generate = AsyncMock(return_value="interested")
    result = run(client.classify("hello", ["interested", "lost"]))
    assert result == "interested"
    assert client.generate.await_args.kwargs["model"] == "local-small"


def test_budget_gate_downgrades_fast_to_ollama():
    """When budget gate returns 'local', fast calls fall back to Ollama."""
    client = make_client()
    client._budget_gate = AsyncMock(return_value="local")
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
