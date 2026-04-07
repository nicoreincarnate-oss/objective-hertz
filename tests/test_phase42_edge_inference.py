"""Tests for shared.edge_inference (Phase 42 — Agent A5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from shared import edge_inference
from shared.edge_inference import (
    cosine_similarity,
    infer_edges_for_recent_nodes,
)


def _node(
    node_id: str,
    embedding: list[float] | None,
    created_at: datetime,
    content: str = "",
) -> dict[str, Any]:
    return {
        "id": node_id,
        "embedding": embedding,
        "created_at": created_at,
        "content": content,
    }


@pytest.mark.asyncio
async def test_no_nodes_no_edges() -> None:
    """Empty DB → returns zero counts and writes nothing."""
    with (
        patch.object(edge_inference.db, "fetch_all", new=AsyncMock(return_value=[])),
        patch.object(edge_inference.db, "fetch_one", new=AsyncMock(return_value=None)) as fetch_one,
        patch.object(edge_inference.db, "execute", new=AsyncMock()) as execute,
    ):
        stats = await infer_edges_for_recent_nodes()

    assert stats == {
        "nodes_examined": 0,
        "edges_inferred": 0,
        "edges_skipped_existing": 0,
        "errors": [],
    }
    fetch_one.assert_not_called()
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_skip_existing_edges() -> None:
    """If an edge already exists, no INSERT is issued."""
    now = datetime.now(UTC)
    nodes = [
        _node("a", [1.0, 0.0, 0.0], now, content="alpha bravo charlie"),
        _node("b", [1.0, 0.0, 0.0], now + timedelta(seconds=10), content="alpha bravo charlie"),
    ]

    with (
        patch.object(edge_inference.db, "fetch_all", new=AsyncMock(return_value=nodes)),
        patch.object(
            edge_inference.db, "fetch_one", new=AsyncMock(return_value={"present": 1})
        ) as fetch_one,
        patch.object(edge_inference.db, "execute", new=AsyncMock()) as execute,
    ):
        stats = await infer_edges_for_recent_nodes()

    assert stats["nodes_examined"] == 2
    assert stats["edges_inferred"] == 0
    assert stats["edges_skipped_existing"] == 1
    assert stats["errors"] == []
    fetch_one.assert_awaited()
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_similarity_threshold() -> None:
    """A pair with cosine ~0.6 must NOT produce a RELATED_TO edge at threshold 0.7.

    Vectors are also constructed so they share NO entity tokens, are far apart
    in time, and so produce no edges of any kind.
    """
    now = datetime.now(UTC)
    # Vectors with cosine ~0.6
    a_vec = [1.0, 0.0]
    b_vec = [0.6, 0.8]  # cosine = 0.6 exactly
    nodes = [
        _node("a", a_vec, now, content="zzzzz"),
        _node("b", b_vec, now + timedelta(hours=2), content="qqqqq"),
    ]

    with (
        patch.object(edge_inference.db, "fetch_all", new=AsyncMock(return_value=nodes)),
        patch.object(edge_inference.db, "fetch_one", new=AsyncMock(return_value=None)),
        patch.object(edge_inference.db, "execute", new=AsyncMock()) as execute,
    ):
        stats = await infer_edges_for_recent_nodes(similarity_threshold=0.7)

    # cos = 0.6 < 0.7 → no RELATED_TO. delta = 2h → no CAUSED_BY. No shared
    # entities → no MENTIONS. Net: no edges.
    assert stats["edges_inferred"] == 0
    assert stats["edges_skipped_existing"] == 0
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_temporal_window_caused_by() -> None:
    """Two nodes within 60s with similarity > 0.5 → CAUSED_BY edge written."""
    now = datetime.now(UTC)
    a_vec = [1.0, 0.0]
    b_vec = [1.0, 0.0]  # cosine = 1.0
    nodes = [
        _node("a", a_vec, now, content="zzzz"),
        _node("b", b_vec, now + timedelta(seconds=10), content="qqqq"),
    ]

    with (
        patch.object(edge_inference.db, "fetch_all", new=AsyncMock(return_value=nodes)),
        patch.object(edge_inference.db, "fetch_one", new=AsyncMock(return_value=None)),
        patch.object(edge_inference.db, "execute", new=AsyncMock()) as execute,
    ):
        stats = await infer_edges_for_recent_nodes(similarity_threshold=0.7)

    # Expect both RELATED_TO (sim=1.0) and CAUSED_BY (within 60s) edges.
    edge_types_written: list[str] = []
    for call in execute.await_args_list:
        params = call.args[1]
        edge_types_written.append(params[2])

    assert "RELATED_TO" in edge_types_written
    assert "CAUSED_BY" in edge_types_written
    assert stats["edges_inferred"] == len(edge_types_written)
    assert stats["edges_inferred"] >= 2

    # CAUSED_BY direction must be older -> newer (a -> b).
    causal_call = next(
        call
        for call in execute.await_args_list
        if call.args[1][2] == "CAUSED_BY"
    )
    src, dst = causal_call.args[1][0], causal_call.args[1][1]
    assert (src, dst) == ("a", "b")


@pytest.mark.asyncio
async def test_dry_run() -> None:
    """`dry_run=True` reports edges but issues zero INSERTs."""
    now = datetime.now(UTC)
    nodes = [
        _node("a", [1.0, 0.0], now, content="hello world"),
        _node("b", [1.0, 0.0], now + timedelta(seconds=5), content="hello world"),
    ]

    with (
        patch.object(edge_inference.db, "fetch_all", new=AsyncMock(return_value=nodes)),
        patch.object(edge_inference.db, "fetch_one", new=AsyncMock(return_value=None)),
        patch.object(edge_inference.db, "execute", new=AsyncMock()) as execute,
    ):
        stats = await infer_edges_for_recent_nodes(dry_run=True)

    assert stats["edges_inferred"] >= 1
    execute.assert_not_called()


def test_cosine_similarity_basic() -> None:
    """Known vectors → known cosine similarity result."""
    # Identical unit vectors → 1.0
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    # Orthogonal → 0.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # Opposite → -1.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    # 0.6 reference: [1,0] vs [0.6,0.8] → 0.6
    assert cosine_similarity([1.0, 0.0], [0.6, 0.8]) == pytest.approx(0.6)
    # Mismatched lengths → 0.0
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0
    # Zero norm → 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
