"""Regression tests for local-first LLM routing."""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch


def run(coro):
    return asyncio.run(coro)


def make_client():
    sys.modules.pop("shared.llm_client", None)
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


def test_generate_fast_prefers_local_ollama():
    client = make_client()

    result = run(client.generate("hello", model="fast"))

    assert result == "local"
    client._ollama_generate.assert_awaited_once()
    client._claude_generate.assert_not_awaited()
    client._record_claude_spend.assert_not_awaited()


def test_generate_smart_keeps_claude_path():
    client = make_client()

    result = run(client.generate("hello", model="smart"))

    assert result == "claude"
    client._claude_generate.assert_awaited_once()
    client._record_claude_spend.assert_awaited_once()


def test_classify_uses_local_small_model():
    client = make_client()
    client.generate = AsyncMock(return_value="interested")

    result = run(client.classify("hello", ["interested", "lost"]))

    assert result == "interested"
    assert client.generate.await_args.kwargs["model"] == "local-small"
