"""Tests for shared/operator_memory_hook.py — Phase 42 / Agent A7.

These tests mock the Neo4j driver, the MAGMA embedding helper, and the
shared.db.execute function so they run without external infrastructure.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Stub upstream modules BEFORE importing the module under test ──────

_fake_db = types.ModuleType("shared.db")
_fake_db.execute = AsyncMock()
sys.modules.setdefault("shared.db", _fake_db)

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
sys.modules.setdefault("shared.config", _fake_config)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="{}"))
sys.modules.setdefault("shared.llm_client", _fake_llm)

# Stub the heavy MAGMA module so importing the hook does not pull in Neo4j.
_fake_magma = types.ModuleType("shared.magma")
_fake_magma._get_driver = MagicMock(return_value=None)
_fake_magma._get_embedding = AsyncMock(return_value=None)
_fake_magma._embedding_hash = MagicMock(return_value="deadbeefcafebabe")
sys.modules["shared.magma"] = _fake_magma

from shared import operator_memory_hook  # noqa: E402
from shared.operator_memory_hook import (  # noqa: E402
    OPERATOR_CONFIDENCE,
    OPERATOR_DAEMON,
    OPERATOR_VISIBILITY,
    record_operator_chat,
    record_operator_decision,
    record_operator_pin,
)

# ── Helpers ──────────────────────────────────────────────────────────


class FakeSession:
    """Capture every Cypher call made through `session.run`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run(self, query: str, **params) -> None:
        self.calls.append((query, params))


class FakeDriver:
    def __init__(self) -> None:
        self.session_obj = FakeSession()

    @contextmanager
    def session(self):
        yield self.session_obj


@pytest.fixture
def fake_driver(monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    driver = FakeDriver()
    monkeypatch.setattr(operator_memory_hook, "_get_driver", lambda: driver)
    # Force embedding path to a no-op so we don't make real network calls.
    monkeypatch.setattr(operator_memory_hook, "_safe_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(operator_memory_hook, "_record_provenance", AsyncMock())
    return driver


# ── Constants sanity check ───────────────────────────────────────────


def test_operator_constants_match_spec():
    assert OPERATOR_DAEMON == "operator"
    assert OPERATOR_VISIBILITY == "tier1"
    assert OPERATOR_CONFIDENCE == 1.0


# ── Test 1: chat creates two nodes + one CAUSED_BY edge ──────────────


def test_record_chat_creates_two_nodes_and_edge(fake_driver: FakeDriver):
    msg_id = asyncio.run(
        record_operator_chat(
            message="Approve the new pricing tier",
            response="Acknowledged — pricing tier approved.",
            intent="approval",
        )
    )

    assert msg_id, "expected a non-empty message node id"
    assert msg_id.startswith("magma_op_")

    calls = fake_driver.session_obj.calls
    create_calls = [c for c in calls if "CREATE (n:MemoryNode" in c[0]]
    edge_calls = [c for c in calls if "CREATE (a)-[r:CAUSED_BY]->(b)" in c[0]]

    assert len(create_calls) == 2, f"expected 2 node creates, got {len(create_calls)}"
    assert len(edge_calls) == 1, f"expected 1 CAUSED_BY edge, got {len(edge_calls)}"

    # Confirm operator metadata is written on every node.
    for _, params in create_calls:
        assert params["source_daemon"] == "operator"
        assert params["visibility"] == "tier1"
        assert params["confidence"] == 1.0

    # Edge must point response → message (response CAUSED_BY message).
    edge_params = edge_calls[0][1]
    assert edge_params["src"] != edge_params["dst"]
    assert edge_params["dst"] == msg_id


# ── Test 2: approve decision writes a SUPPORTS edge ──────────────────


def test_record_decision_approve_writes_supports_edge(fake_driver: FakeDriver):
    node_id = asyncio.run(
        record_operator_decision(
            decision_type="approve",
            target_id="magma_target_001",
            rationale="Looks correct",
        )
    )

    assert node_id.startswith("magma_op_")

    calls = fake_driver.session_obj.calls
    supports_edges = [c for c in calls if "CREATE (a)-[r:SUPPORTS]->(b)" in c[0]]
    contradicts_edges = [c for c in calls if "CREATE (a)-[r:CONTRADICTS]->(b)" in c[0]]

    assert len(supports_edges) == 1, "approve must emit exactly one SUPPORTS edge"
    assert len(contradicts_edges) == 0, "approve must not emit CONTRADICTS"

    edge_params = supports_edges[0][1]
    assert edge_params["src"] == node_id
    assert edge_params["dst"] == "magma_target_001"
    assert edge_params["props"]["decision_type"] == "approve"


# ── Test 3: reject decision writes a CONTRADICTS edge ────────────────


def test_record_decision_reject_writes_contradicts_edge(fake_driver: FakeDriver):
    node_id = asyncio.run(
        record_operator_decision(
            decision_type="REJECT",  # uppercase to confirm case-insensitive normalisation
            target_id="magma_target_002",
            rationale="Wrong assumption",
        )
    )

    assert node_id.startswith("magma_op_")

    calls = fake_driver.session_obj.calls
    contradicts_edges = [c for c in calls if "CREATE (a)-[r:CONTRADICTS]->(b)" in c[0]]
    supports_edges = [c for c in calls if "CREATE (a)-[r:SUPPORTS]->(b)" in c[0]]

    assert len(contradicts_edges) == 1, "reject must emit exactly one CONTRADICTS edge"
    assert len(supports_edges) == 0, "reject must not emit SUPPORTS"

    edge_params = contradicts_edges[0][1]
    assert edge_params["src"] == node_id
    assert edge_params["dst"] == "magma_target_002"
    assert edge_params["props"]["decision_type"] == "reject"


# ── Test 4: pin updates existing node + creates annotation ───────────


def test_record_pin_updates_existing_node(fake_driver: FakeDriver):
    annotation_id = asyncio.run(
        record_operator_pin(
            node_id="magma_target_xyz",
            note="critical context",
        )
    )

    assert annotation_id.startswith("magma_op_")

    calls = fake_driver.session_obj.calls
    update_calls = [c for c in calls if "SET n.pinned = true" in c[0]]
    create_calls = [c for c in calls if "CREATE (n:MemoryNode" in c[0]]
    mention_edges = [c for c in calls if "CREATE (a)-[r:MENTIONS]->(b)" in c[0]]

    assert len(update_calls) == 1, "pin must update the target node exactly once"
    assert update_calls[0][1]["node_id"] == "magma_target_xyz"
    assert update_calls[0][1]["daemon"] == "operator"

    assert len(create_calls) == 1, "pin must create a single annotation node"
    assert len(mention_edges) == 1, "pin must create exactly one MENTIONS edge"

    edge_params = mention_edges[0][1]
    assert edge_params["src"] == annotation_id
    assert edge_params["dst"] == "magma_target_xyz"


# ── Defensive validation ─────────────────────────────────────────────


def test_chat_rejects_empty_message(fake_driver: FakeDriver):
    with pytest.raises(ValueError):
        asyncio.run(record_operator_chat(message="", response="ok"))


def test_decision_rejects_empty_target(fake_driver: FakeDriver):
    with pytest.raises(ValueError):
        asyncio.run(record_operator_decision(decision_type="approve", target_id=""))


def test_pin_rejects_empty_node(fake_driver: FakeDriver):
    with pytest.raises(ValueError):
        asyncio.run(record_operator_pin(node_id=""))


def test_no_driver_returns_empty_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(operator_memory_hook, "_get_driver", lambda: None)
    monkeypatch.setattr(operator_memory_hook, "_safe_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(operator_memory_hook, "_record_provenance", AsyncMock())

    assert asyncio.run(record_operator_chat("hi", "hello")) == ""
    assert asyncio.run(record_operator_decision("approve", "magma_x")) == ""
    assert asyncio.run(record_operator_pin("magma_x")) == ""
