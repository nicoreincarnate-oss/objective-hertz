"""Behavioral eval: budget guard MUST fail closed on DB error.

When fetch_val raises ConnectionError (DB unreachable), the budget guard
must reject the API call and fall back to Ollama -- never allow the call through.
This is a P0 safety property per AEGIS audit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_budget_guard_fails_closed_on_db_error(eval_recorder):
    """Budget guard returns 'local' (Ollama fallback) when DB is unreachable."""
    from shared.middleware import check_budget_for_llm_call

    with patch("shared.db.fetch_val", side_effect=ConnectionError("DB down")):
        result = await check_budget_for_llm_call("smart")
        assert result in ("local", "local-small"), (
            "Budget guard MUST fail closed on DB error. "
            f"Got '{result}' instead of Ollama fallback."
        )

    await eval_recorder.record(
        suite="budget",
        scenario="fails_closed_on_db_error",
        passed=True,
    )


@pytest.mark.asyncio
async def test_budget_guard_fails_closed_on_unexpected_exception(eval_recorder):
    """Budget guard returns 'local' on any unexpected exception, not just ConnectionError."""
    from shared.middleware import check_budget_for_llm_call

    with patch("shared.db.fetch_val", side_effect=RuntimeError("unexpected")):
        result = await check_budget_for_llm_call("fast")
        assert result in ("local", "local-small"), (
            "Budget guard MUST fail closed on ANY DB error, not just ConnectionError. "
            f"Got '{result}'."
        )

    await eval_recorder.record(
        suite="budget",
        scenario="fails_closed_on_unexpected_exception",
        passed=True,
    )
