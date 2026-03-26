"""Tests for perseus/sleep_cycle.py — nightly self-optimization."""

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, patch, MagicMock

_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.execute = AsyncMock()
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_one = AsyncMock(return_value=None)
_fake_db.fetch_val = AsyncMock(return_value=0)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.init_pool = AsyncMock()
_fake_db.close_pool = AsyncMock()
_fake_db.insert_task = AsyncMock(return_value=1)
_fake_db.transaction = AsyncMock()

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(
    root_dir="/tmp/test-repo",
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="", mem0_host="http://localhost:8888", qdrant_host="http://localhost:6333", qdrant_collection="test", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
    budget=types.SimpleNamespace(monthly_cap=800),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"proposals": []}'))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.broadcast = AsyncMock()
_fake_comms.store_learning = AsyncMock()

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from perseus.sleep_cycle import _filter_surviving


# ── _filter_surviving ──

def test_filter_surviving_keeps_approved():
    proposals = [
        {"what": "change pricing", "where": "system_config", "confidence": 0.8},
        {"what": "update template", "where": "soul_copy.md", "confidence": 0.6},
    ]
    verdicts = [
        {"proposal_index": 0, "verdict": "approve", "counterargument": ""},
        {"proposal_index": 1, "verdict": "reject", "counterargument": "too risky"},
    ]
    surviving = _filter_surviving(proposals, verdicts)
    assert len(surviving) == 1
    assert surviving[0]["what"] == "change pricing"

def test_filter_surviving_applies_modifications():
    proposals = [{"what": "set price to $500", "where": "system_config", "confidence": 0.7}]
    verdicts = [{"proposal_index": 0, "verdict": "modify", "modified_proposal": {"what": "set price to $400", "where": "system_config", "confidence": 0.7}}]
    surviving = _filter_surviving(proposals, verdicts)
    assert len(surviving) == 1
    assert "400" in surviving[0]["what"]

def test_filter_surviving_empty_proposals():
    assert _filter_surviving([], []) == []

def test_filter_surviving_all_rejected():
    proposals = [{"what": "bad idea", "where": "soul", "confidence": 0.3}]
    verdicts = [{"proposal_index": 0, "verdict": "reject", "counterargument": "nope"}]
    assert _filter_surviving(proposals, verdicts) == []

def test_filter_surviving_verdict_out_of_range():
    proposals = [{"what": "ok idea", "where": "config", "confidence": 0.6}]
    verdicts = [{"proposal_index": 5, "verdict": "approve"}]  # index 5 doesn't exist
    surviving = _filter_surviving(proposals, verdicts)
    assert len(surviving) == 0
