"""Tests for the Phase 42 nightly consolidator (Stage C)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from shared import consolidator

# ───────────────────────── helpers ──────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_event(eid: int, etype: str, summary: str) -> dict[str, Any]:
    return {
        "id": eid,
        "event_type": etype,
        "payload": {"summary": summary},
        "created_at": None,
    }


# ───────────────────────── unit-level helpers ──────────────────────────


def test_cosine_basic():
    assert consolidator._cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert consolidator._cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    # Length mismatch → 0.0, never raises.
    assert consolidator._cosine([1.0, 2.0], [1.0]) == 0.0
    # Zero vector → 0.0.
    assert consolidator._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_event_text_pulls_summary_from_payload():
    ev = _make_event(1, "lead.created", "New lead from referral")
    text = consolidator._event_text(ev)
    assert "lead.created" in text
    assert "New lead from referral" in text


def test_greedy_cluster_groups_by_similarity():
    # Two clear clusters: three vectors near (1,0) and three near (0,1).
    embeddings = [
        [1.0, 0.05],
        [0.95, 0.10],
        [0.90, 0.05],
        [0.05, 1.0],
        [0.10, 0.95],
        [0.05, 0.90],
    ]
    clusters = consolidator._greedy_cluster(embeddings, threshold=0.6)
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [3, 3]


def test_greedy_cluster_empty():
    assert consolidator._greedy_cluster([]) == []


# ───────────────────────── public API ──────────────────────────


def test_consolidate_with_no_events():
    """Empty events table → zero counts and no errors."""
    with patch("shared.db.fetch_all", new=AsyncMock(return_value=[])):
        result = _run(consolidator.consolidate_recent_events())

    assert result["events_examined"] == 0
    assert result["clusters_found"] == 0
    assert result["memories_created"] == 0
    assert result["errors"] == []


def test_clustering_groups_similar_events():
    """Mocked embeddings with two clear clusters → both summarised + persisted."""
    events = [
        _make_event(1, "lead.created", "lead a"),
        _make_event(2, "lead.created", "lead b"),
        _make_event(3, "lead.created", "lead c"),
        _make_event(4, "lead.created", "lead d"),
        _make_event(5, "lead.created", "lead e"),
        _make_event(6, "deploy.completed", "deploy a"),
        _make_event(7, "deploy.completed", "deploy b"),
        _make_event(8, "deploy.completed", "deploy c"),
        _make_event(9, "deploy.completed", "deploy d"),
        _make_event(10, "deploy.completed", "deploy e"),
    ]

    # Two distinct embedding clusters.
    embedding_map = {
        "lead.created: lead a": [1.0, 0.05],
        "lead.created: lead b": [0.98, 0.04],
        "lead.created: lead c": [0.97, 0.06],
        "lead.created: lead d": [0.99, 0.03],
        "lead.created: lead e": [0.96, 0.05],
        "deploy.completed: deploy a": [0.05, 1.0],
        "deploy.completed: deploy b": [0.04, 0.98],
        "deploy.completed: deploy c": [0.06, 0.97],
        "deploy.completed: deploy d": [0.03, 0.99],
        "deploy.completed: deploy e": [0.05, 0.96],
    }

    async def fake_embed(text: str):
        return embedding_map.get(text, [0.5, 0.5])

    async def fake_summarize(prompt: str):
        return "Test summary."

    fetch_mock = AsyncMock(return_value=events)
    execute_mock = AsyncMock(return_value=None)

    with (
        patch("shared.db.fetch_all", fetch_mock),
        patch("shared.db.execute", execute_mock),
        patch.object(consolidator, "_embed_text", side_effect=fake_embed),
        patch.object(consolidator, "_llm_summarize", side_effect=fake_summarize),
    ):
        result = _run(
            consolidator.consolidate_recent_events(min_cluster_size=5, dry_run=False)
        )

    assert result["events_examined"] == 10
    assert result["clusters_found"] == 2
    assert result["memories_created"] == 2
    assert result["errors"] == []
    # Persistence should have been called: at least one INSERT per memory plus
    # provenance + edges.  Just verify the mock was used.
    assert execute_mock.await_count > 0


def test_dry_run_does_not_write():
    """dry_run=True should plan memories but never call execute()."""
    events = [_make_event(i, "lead.created", f"lead {i}") for i in range(1, 6)]

    async def fake_embed(text: str):
        return [1.0, 0.0]

    async def fake_summarize(prompt: str):
        return "Cluster summary"

    fetch_mock = AsyncMock(return_value=events)
    execute_mock = AsyncMock(return_value=None)

    with (
        patch("shared.db.fetch_all", fetch_mock),
        patch("shared.db.execute", execute_mock),
        patch.object(consolidator, "_embed_text", side_effect=fake_embed),
        patch.object(consolidator, "_llm_summarize", side_effect=fake_summarize),
    ):
        result = _run(
            consolidator.consolidate_recent_events(min_cluster_size=5, dry_run=True)
        )

    assert result["events_examined"] == 5
    assert result["clusters_found"] == 1
    assert result["memories_created"] == 1
    assert result["errors"] == []
    # Critical: dry-run path must not call the DB write helper at all.
    assert execute_mock.await_count == 0


def test_summarize_failure_records_error_and_continues():
    """If the LLM summariser returns None the cluster is skipped, not aborted."""
    events = [_make_event(i, "lead.created", f"lead {i}") for i in range(1, 6)]

    async def fake_embed(text: str):
        return [1.0, 0.0]

    async def fake_summarize_none(prompt: str):
        return None

    fetch_mock = AsyncMock(return_value=events)
    execute_mock = AsyncMock(return_value=None)

    with (
        patch("shared.db.fetch_all", fetch_mock),
        patch("shared.db.execute", execute_mock),
        patch.object(consolidator, "_embed_text", side_effect=fake_embed),
        patch.object(consolidator, "_llm_summarize", side_effect=fake_summarize_none),
    ):
        result = _run(
            consolidator.consolidate_recent_events(min_cluster_size=5, dry_run=False)
        )

    assert result["events_examined"] == 5
    assert result["clusters_found"] == 1
    assert result["memories_created"] == 0
    assert any("llm summarize failed" in e for e in result["errors"])
