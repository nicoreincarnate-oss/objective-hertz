"""Tests for Week 4: Misalignment probes, trajectory analysis, convergence, inference optimizer."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

os.environ["MISALIGNMENT_PROBES_ENABLED"] = "1"
os.environ["INFERENCE_OPTIMIZATION"] = "1"

_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])

_fake_config = types.ModuleType("shared.config")
from pathlib import Path
_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="", qdrant_host="", qdrant_collection="", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="ok"))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from perseus.misalignment_probe import (
    KNOWN_HARMFUL_PATTERNS,
    _check_pricing_drift,
    check_forgetting_risk,
    convergence_detector,
    probe_proposal,
    trajectory_analysis,
)
from shared.inference_optimizer import (
    compress_context,
    estimate_kv_cache_usage,
    select_inference_mode,
    should_compress_context,
)


# ═══════════════════════════════════════════════════════════════
# Probe Proposal
# ═══════════════════════════════════════════════════════════════

def test_probe_safe_proposal():
    proposal = {"what": "adjust email send time", "where": "system_config", "confidence": 0.8}
    result = asyncio.run(probe_proposal(proposal, {}))
    assert result["safe"] is True
    assert result["risk_score"] < 0.15

def test_probe_harmful_pattern():
    proposal = {"what": "disable compliance checks", "where": "system_config"}
    result = asyncio.run(probe_proposal(proposal, {}))
    assert result["safe"] is False
    assert "harmful_pattern" in result["flags"][0]

def test_probe_pricing_drift():
    proposal = {"what": "change pricing", "old_value": 299, "new_value": 450, "where": "config"}
    result = asyncio.run(probe_proposal(proposal, {}))
    assert result["risk_score"] > 0
    assert any("pricing" in f for f in result["flags"])

def test_probe_soul_doc_modification():
    proposal = {"what": "update tone", "where": "soul/soul_copy.md", "confidence": 0.7}
    result = asyncio.run(probe_proposal(proposal, {}))
    assert any("soul" in f for f in result["flags"])

def test_probe_low_confidence():
    proposal = {"what": "something", "where": "config", "confidence": 0.2}
    result = asyncio.run(probe_proposal(proposal, {}))
    assert any("low_confidence" in f for f in result["flags"])


# ═══════════════════════════════════════════════════════════════
# Pricing Drift
# ═══════════════════════════════════════════════════════════════

def test_pricing_drift_no_price():
    assert _check_pricing_drift({"what": "update template"}, {}) == 0.0

def test_pricing_drift_small():
    assert _check_pricing_drift({"what": "change pricing", "old_value": 299, "new_value": 320}, {}) == 0.0

def test_pricing_drift_large():
    result = _check_pricing_drift({"what": "change pricing", "old_value": 299, "new_value": 450}, {})
    assert result > 0


# ═══════════════════════════════════════════════════════════════
# Trajectory Analysis
# ═══════════════════════════════════════════════════════════════

def test_trajectory_insufficient_data():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(trajectory_analysis(days=7))
    assert result["alert"] is False

def test_trajectory_stable():
    from unittest.mock import patch
    mock_data = [
        {"cycle_date": "2026-03-20", "applied_changes": "[]", "rolled_back": False},
        {"cycle_date": "2026-03-21", "applied_changes": "[]", "rolled_back": False},
    ]
    with patch("shared.db.fetch_all", new=AsyncMock(return_value=mock_data)):
        result = asyncio.run(trajectory_analysis(days=7))
        assert result["trend"] in ("stable", "no_changes")


# ═══════════════════════════════════════════════════════════════
# Convergence Detector
# ═══════════════════════════════════════════════════════════════

def test_convergence_insufficient_data():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(convergence_detector(days=14))
    assert result["converged"] is False

def test_convergence_healthy():
    """Diverse categories → not converged."""
    from unittest.mock import patch
    mock_data = [
        {"applied_changes": '[{"category":"pricing"},{"category":"email"}]'},
        {"applied_changes": '[{"category":"targeting"},{"category":"compliance"}]'},
        {"applied_changes": '[{"category":"discovery"},{"category":"delivery"}]'},
    ]
    with patch("shared.db.fetch_all", new=AsyncMock(return_value=mock_data)):
        result = asyncio.run(convergence_detector(days=14))
        assert result["converged"] is False
        assert result["entropy"] > 0.5

def test_convergence_repetitive():
    """Same category repeated → converged."""
    from unittest.mock import patch
    mock_data = [
        {"applied_changes": '[{"category":"pricing"}]'},
        {"applied_changes": '[{"category":"pricing"}]'},
        {"applied_changes": '[{"category":"pricing"}]'},
    ]
    with patch("shared.db.fetch_all", new=AsyncMock(return_value=mock_data)):
        result = asyncio.run(convergence_detector(days=14))
        assert result["converged"] is True
        assert result["entropy"] < 0.3


# ═══════════════════════════════════════════════════════════════
# Forgetting Risk
# ═══════════════════════════════════════════════════════════════

def test_forgetting_risk_no_rules():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    risk = asyncio.run(check_forgetting_risk({"new_value": "always discount prices"}))
    assert risk == 0.0

def test_forgetting_risk_no_conflict():
    _fake_db.fetch_all = AsyncMock(return_value=[
        {"rule_text": "Always mention local competitors for restaurants", "confidence": 0.9}
    ])
    risk = asyncio.run(check_forgetting_risk({"new_value": "Use friendly tone in emails"}))
    assert risk < 0.5


# ═══════════════════════════════════════════════════════════════
# Inference Optimizer
# ═══════════════════════════════════════════════════════════════

def test_kv_cache_estimate():
    usage = estimate_kv_cache_usage(1000)
    assert usage == 256000  # 1000 * 256

def test_should_compress_short():
    assert should_compress_context("Hello world") is False

def test_should_compress_huge():
    # 10MB of text → should definitely compress
    assert should_compress_context("x" * 10_000_000) is True

def test_compress_context_dedup():
    memories = ["Same memory here", "Same memory here", "Different memory"]
    result = compress_context(memories, budget_tokens=500)
    assert len(result) <= 2  # deduped

def test_compress_context_budget():
    memories = ["x" * 1000 for _ in range(50)]
    result = compress_context(memories, budget_tokens=100)
    total_chars = sum(len(m) for m in result)
    assert total_chars < 1000  # budget enforced

def test_compress_context_empty():
    assert compress_context([]) == []

def test_select_mode_complex():
    assert select_inference_mode("complex") == "standard"

def test_select_mode_simple():
    assert select_inference_mode("simple") == "efficient"

def test_select_mode_budget_pressure():
    assert select_inference_mode("complex", budget_remaining_pct=0.1) == "efficient"

def test_select_mode_long_prompt():
    assert select_inference_mode("medium", prompt_tokens=5000) == "efficient"


# ═══════════════════════════════════════════════════════════════
# Harmful Patterns
# ═══════════════════════════════════════════════════════════════

def test_harmful_patterns_exist():
    assert len(KNOWN_HARMFUL_PATTERNS) >= 10

def test_harmful_patterns_lowercase():
    for p in KNOWN_HARMFUL_PATTERNS:
        assert p == p.lower()
