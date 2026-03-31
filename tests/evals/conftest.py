"""Shared fixtures for behavioral evals. All evals use mocked LLM responses -- zero real API calls."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns canned responses. No real API calls."""
    client = AsyncMock()
    client.generate.return_value = {"content": "mocked response", "cost_usd": 0.001}
    return client


@pytest.fixture
def mock_db_pool():
    """Mock database pool with configurable fetch/execute."""
    pool = AsyncMock()
    pool.fetch_val = AsyncMock(return_value=None)
    pool.fetch_all = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def eval_recorder(mock_db_pool):
    """Records eval results to mock DB (or real DB when BEHAVIORAL_EVALS_ENABLED)."""
    from tests.evals.recorder import EvalRecorder

    return EvalRecorder(db=mock_db_pool)


@pytest.fixture(autouse=True)
def disable_real_services(monkeypatch):
    """Safety net: ensure no real external calls during evals."""
    monkeypatch.setenv("BEHAVIORAL_EVALS_ENABLED", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
