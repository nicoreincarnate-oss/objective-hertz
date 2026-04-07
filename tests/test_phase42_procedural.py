"""Tests for shared.procedural_extractor (Phase 42 — Agent A4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from shared import procedural_extractor
from shared.procedural_extractor import (
    compute_procedure_signature,
    extract_procedure_from_task,
)

SAMPLE_CALLS: list[dict[str, Any]] = [
    {"tool": "firecrawl", "args": {"url": "https://example.com"}, "result_summary": "ok"},
    {"tool": "llm.summarize", "args": {"text": "..."}, "result_summary": "ok"},
    {"tool": "db.insert", "args": {"table": "leads"}, "result_summary": "ok"},
]


@pytest.mark.asyncio
async def test_skip_failed_tasks() -> None:
    """success=False must short-circuit and never touch the database."""
    with patch.object(procedural_extractor.db, "fetch_one", new=AsyncMock()) as mock_fetch, \
         patch.object(procedural_extractor.db, "execute", new=AsyncMock()) as mock_exec:
        result = await extract_procedure_from_task(
            daemon="titan",
            task_id="task-1",
            task_summary="failed run",
            tool_calls=SAMPLE_CALLS,
            success=False,
            duration_seconds=1.0,
        )

    assert result is None
    mock_fetch.assert_not_called()
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_skip_trivial_tasks() -> None:
    """Tasks with fewer than MIN_TOOL_CALLS_FOR_PROCEDURE calls are skipped."""
    with patch.object(procedural_extractor.db, "fetch_one", new=AsyncMock()) as mock_fetch, \
         patch.object(procedural_extractor.db, "execute", new=AsyncMock()) as mock_exec:
        result = await extract_procedure_from_task(
            daemon="titan",
            task_id="task-2",
            task_summary="trivial",
            tool_calls=[{"tool": "echo"}],
            success=True,
            duration_seconds=0.5,
        )

    assert result is None
    mock_fetch.assert_not_called()
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_signature_matches_existing() -> None:
    """When a matching signature exists we increment counters and reuse the ID."""
    fetch_mock = AsyncMock(return_value={"id": "existing-node-123"})
    exec_mock = AsyncMock()

    with patch.object(procedural_extractor.db, "fetch_one", new=fetch_mock), \
         patch.object(procedural_extractor.db, "execute", new=exec_mock):
        result = await extract_procedure_from_task(
            daemon="titan",
            task_id="task-3",
            task_summary="re-run",
            tool_calls=SAMPLE_CALLS,
            success=True,
            duration_seconds=2.5,
        )

    assert result == "existing-node-123"
    # Exactly one SELECT and one UPDATE — no INSERTs.
    assert fetch_mock.await_count == 1
    assert exec_mock.await_count == 1
    update_sql = exec_mock.await_args_list[0].args[0]
    assert "UPDATE magma_nodes" in update_sql
    assert "access_count" in update_sql


@pytest.mark.asyncio
async def test_creates_new_procedure() -> None:
    """When no signature match exists, INSERT happens and the new ID is returned."""
    # First fetch_one (lookup) → None. Second fetch_one (insert returning id) → new row.
    fetch_mock = AsyncMock(side_effect=[None, {"id": "new-node-999"}])
    exec_mock = AsyncMock()

    async def fake_summary(task_summary: str, tool_calls: list[dict[str, Any]]) -> str:
        return "synthesized summary"

    async def fake_embedding(text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    with patch.object(procedural_extractor.db, "fetch_one", new=fetch_mock), \
         patch.object(procedural_extractor.db, "execute", new=exec_mock), \
         patch.object(procedural_extractor, "_generate_summary", new=fake_summary), \
         patch.object(procedural_extractor, "_generate_embedding", new=fake_embedding):
        result = await extract_procedure_from_task(
            daemon="hermes",
            task_id="task-4",
            task_summary="novel workflow",
            tool_calls=SAMPLE_CALLS,
            success=True,
            duration_seconds=4.2,
        )

    assert result == "new-node-999"
    # Two fetch_one calls: SELECT lookup + INSERT...RETURNING.
    assert fetch_mock.await_count == 2

    insert_sql = fetch_mock.await_args_list[1].args[0]
    assert "INSERT INTO magma_nodes" in insert_sql
    assert "'procedure'" in insert_sql

    # Provenance + edge inserts went through db.execute.
    executed_sqls = [call.args[0] for call in exec_mock.await_args_list]
    assert any("magma_provenance" in sql for sql in executed_sqls)
    assert any("magma_edges" in sql and "OWNED_BY" in sql for sql in executed_sqls)


def test_signature_is_deterministic() -> None:
    """Identical inputs must produce identical signatures; order matters."""
    sig_a = compute_procedure_signature(SAMPLE_CALLS)
    sig_b = compute_procedure_signature(
        [
            {"tool": "firecrawl", "args": {"url": "different"}},
            {"tool": "llm.summarize", "args": {"text": "different"}},
            {"tool": "db.insert", "args": {"table": "different"}},
        ]
    )
    # Same tool order → same signature even with different args.
    assert sig_a == sig_b
    assert sig_a.startswith("proc:")

    reordered = compute_procedure_signature(list(reversed(SAMPLE_CALLS)))
    assert reordered != sig_a
