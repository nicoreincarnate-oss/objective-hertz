"""Tests for shared/magma.py — MAGMA memory system."""

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# Fake modules
_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.execute = AsyncMock()
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(
    memory=types.SimpleNamespace(
        magma_enabled=False,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        qdrant_host="http://localhost:6333",
        qdrant_collection="test",
        mem0_host="http://localhost:8888",
        zep_url="http://localhost:8000",
        zep_enabled=False,
    ),
    ollama=types.SimpleNamespace(
        host="http://localhost:11434",
        embed_model="nomic-embed-text",
    ),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="{}"))

sys.modules.setdefault("shared.db", _fake_db)
sys.modules.setdefault("shared.config", _fake_config)
sys.modules.setdefault("shared.llm_client", _fake_llm)

# Must import AFTER faking modules
from shared.magma import (
    Intent,
    _cosine_similarity,
    _embedding_hash,
    _extract_entities,
    _linearize_with_provenance,
    _parse_time_range,
    _segment_events,
    compute_anchor_confidence,
    source_reliability_score,
    temporal_decay_factor,
)


# ── _segment_events ──

def test_segment_events_short_content():
    assert _segment_events("Hello world") == ["Hello world"]

def test_segment_events_no_boundaries():
    text = "A" * 250
    assert _segment_events(text) == [text]

def test_segment_events_numbered_list():
    text = "Overview of what happened today in the pipeline system:\n1. First thing that happened in the pipeline where leads were discovered and processed through the system automatically\n2. Second thing that happened next where emails were composed and sent via Instantly to all researched leads\n3. Third thing completed where follow-ups were processed and replies were classified by the LLM"
    segments = _segment_events(text)
    assert len(segments) >= 2

def test_segment_events_bullet_list():
    text = "Results of today's pipeline execution across all stages:\n- Lead discovered in Austin Texas area for dental practice without website presence in the market\n- Email sent to business owner John Smith regarding custom website proposal for their practice\n- Reply received with positive interest from the business owner wanting to schedule a demo call"
    segments = _segment_events(text)
    assert len(segments) >= 2

def test_segment_events_caps_at_10():
    text = "\n".join(f"{i}. Event number {i} with enough content to pass the 20 char filter" for i in range(15))
    segments = _segment_events(text)
    assert len(segments) <= 10


# ── _extract_entities ──

def test_extract_entities_from_metadata():
    entities = _extract_entities("some content", {
        "business_name": "Acme Corp",
        "industry": "dental",
        "client_id": 42,
    })
    assert "Acme Corp" in entities
    assert "dental" in entities
    assert "client:42" in entities

def test_extract_entities_empty():
    entities = _extract_entities("some content", {})
    assert entities == []

def test_extract_entities_skips_short_values():
    entities = _extract_entities("content", {"business_name": "A", "industry": "OK"})
    assert "A" not in entities
    assert "OK" in entities


# ── _cosine_similarity ──

def test_cosine_similarity_identical():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine_similarity(v, v) - 1.0) < 0.001

def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(_cosine_similarity(a, b)) < 0.001

def test_cosine_similarity_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(_cosine_similarity(a, b) - (-1.0)) < 0.001

def test_cosine_similarity_empty():
    assert _cosine_similarity([], []) == 0.0

def test_cosine_similarity_mismatched_lengths():
    assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0


# ── _embedding_hash ──

def test_embedding_hash_deterministic():
    v = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    assert _embedding_hash(v) == _embedding_hash(v)

def test_embedding_hash_different_vectors():
    a = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    b = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]
    assert _embedding_hash(a) != _embedding_hash(b)

def test_embedding_hash_is_16_chars():
    v = [0.1] * 8
    assert len(_embedding_hash(v)) == 16


# ── _parse_time_range ──

def test_parse_time_range_today():
    start, end = _parse_time_range("what happened today")
    assert start is not None
    assert end is not None
    assert "T00:00:00" in start

def test_parse_time_range_yesterday():
    start, end = _parse_time_range("show me yesterday results")
    assert start is not None
    assert "T00:00:00" in start

def test_parse_time_range_last_week():
    start, end = _parse_time_range("what happened last week")
    assert start is not None
    assert end is not None

def test_parse_time_range_month_name():
    start, end = _parse_time_range("results from march 2026")
    assert start is not None
    assert "2026-03" in start

def test_parse_time_range_no_match():
    start, end = _parse_time_range("why did leads churn")
    assert start is None
    assert end is None


# ── _linearize_with_provenance ──

def test_linearize_empty():
    result = _linearize_with_provenance([], Intent.SEMANTIC)
    assert "No relevant memories" in result

def test_linearize_semantic_has_provenance():
    nodes = [{"content": "Test memory content", "provenance": "qdrant", "score": 0.8}]
    result = _linearize_with_provenance(nodes, Intent.SEMANTIC)
    assert "[qdrant]" in result
    assert "Test memory" in result

def test_linearize_causal_chain_order():
    nodes = [
        {"content": "Effect happened", "provenance": "neo4j:CAUSED", "score": 0.9, "via_anchor": "a1", "timestamp": "2026-03-02", "mechanism": "direct causation"},
        {"content": "Cause happened first", "provenance": "neo4j", "score": 0.7, "timestamp": "2026-03-01"},
    ]
    result = _linearize_with_provenance(nodes, Intent.CAUSAL)
    assert "CAUSAL CHAIN" in result

def test_linearize_respects_budget():
    # Create many nodes that exceed budget
    nodes = [{"content": "X" * 200, "provenance": "qdrant", "score": 0.5} for _ in range(50)]
    result = _linearize_with_provenance(nodes, Intent.SEMANTIC)
    assert len(result) < 3000  # Should be budget-limited

def test_linearize_dedup():
    nodes = [
        {"content": "Same content here", "provenance": "qdrant", "score": 0.9},
        {"content": "Same content here", "provenance": "neo4j", "score": 0.5},
    ]
    result = _linearize_with_provenance(nodes, Intent.SEMANTIC)
    # Should only appear once (deduped by content[:50])
    assert result.count("Same content") == 1

def test_linearize_temporal_sorted_by_time():
    nodes = [
        {"content": "Old event", "provenance": "neo4j", "score": 0.5, "timestamp": "2026-01-01T00:00:00", "category": "email"},
        {"content": "New event", "provenance": "neo4j", "score": 0.5, "timestamp": "2026-03-01T00:00:00", "category": "email"},
    ]
    result = _linearize_with_provenance(nodes, Intent.TEMPORAL)
    assert "TIMELINE" in result
    # New event should come first (most recent first)
    new_pos = result.find("New event")
    old_pos = result.find("Old event")
    assert new_pos < old_pos


# ── source_reliability_score (MMA paper) ──

def test_reliability_fused_highest():
    assert source_reliability_score("fused", "email") > source_reliability_score("qdrant", "email")

def test_reliability_graph_above_qdrant():
    assert source_reliability_score("graph", "email") > source_reliability_score("qdrant", "email")

def test_reliability_unknown_source():
    assert source_reliability_score("unknown", "email") == 0.5

def test_reliability_traversal_lowest_named():
    assert source_reliability_score("traversal", "email") < source_reliability_score("graph", "email")


# ── temporal_decay_factor ──

def test_decay_recent_is_high():
    from datetime import datetime
    now = datetime.now().isoformat()
    assert temporal_decay_factor(now) > 0.9

def test_decay_old_is_low():
    assert temporal_decay_factor("2024-01-01T00:00:00") < 0.1

def test_decay_empty_string():
    assert temporal_decay_factor("") == 0.5

def test_decay_half_life():
    from datetime import datetime, timedelta
    half_life_ago = (datetime.now() - timedelta(days=14)).isoformat()
    factor = temporal_decay_factor(half_life_ago, half_life_days=14.0)
    assert 0.4 < factor < 0.6  # should be ~0.5 at half-life


# ── compute_anchor_confidence ──

def test_anchor_confidence_high_score_recent_fused():
    anchor = {"source": "fused", "category": "email", "timestamp": "2026-03-24T00:00:00", "importance": 0.9, "score": 1.0}
    conf = compute_anchor_confidence(anchor)
    assert conf > 0.3  # high confidence

def test_anchor_confidence_low_score_old_traversal():
    anchor = {"source": "traversal", "category": "email", "timestamp": "2024-01-01T00:00:00", "importance": 0.1, "score": 0.1}
    conf = compute_anchor_confidence(anchor)
    assert conf < 0.1  # low confidence

def test_anchor_confidence_missing_fields():
    anchor = {"score": 0.5}
    conf = compute_anchor_confidence(anchor)
    assert 0.0 <= conf <= 1.0  # shouldn't crash


# ── Graceful degradation ──

def test_magma_disabled_returns_empty():
    """When MAGMA_ENABLED=False, magma_ingest should return empty list."""
    from shared.magma import magma_ingest
    result = asyncio.run(magma_ingest("test content", "test_category"))
    assert result == []
