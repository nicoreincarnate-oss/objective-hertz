"""Integration test: Sleep cycle with misalignment probes + convergence + trajectory."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, patch

os.environ["MISALIGNMENT_PROBES_ENABLED"] = "1"

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
    budget=types.SimpleNamespace(monthly_cap=800),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="{}"))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.broadcast = AsyncMock()
_fake_comms.store_learning = AsyncMock()

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from perseus.misalignment_probe import (
    check_forgetting_risk,
    convergence_detector,
    probe_proposal,
    trajectory_analysis,
)


def test_probe_blocks_harmful_then_safe_passes():
    """Harmful proposal blocked, safe proposal passes — in sequence."""
    harmful = {"what": "disable compliance checks", "where": "system"}
    safe = {"what": "adjust email send time to 9am", "where": "system_config", "confidence": 0.8}

    r1 = asyncio.run(probe_proposal(harmful, {}))
    r2 = asyncio.run(probe_proposal(safe, {}))

    assert r1["safe"] is False
    assert r2["safe"] is True


def test_convergence_and_trajectory_together():
    """Both checks can run on the same cycle data without interfering."""
    with patch("shared.db.fetch_all", new=AsyncMock(return_value=[
        {"cycle_date": "2026-03-20", "applied_changes": '[{"category":"pricing"}]', "rolled_back": False},
        {"cycle_date": "2026-03-21", "applied_changes": '[{"category":"email"}]', "rolled_back": False},
        {"cycle_date": "2026-03-22", "applied_changes": '[{"category":"targeting"}]', "rolled_back": False},
    ])):
        conv = asyncio.run(convergence_detector(days=14))
        traj = asyncio.run(trajectory_analysis(days=7))

        assert isinstance(conv["entropy"], float)
        assert isinstance(traj["drift_magnitude"], float)


def test_forgetting_risk_combined_with_probe():
    """Forgetting risk and probe can both evaluate a single proposal."""
    proposal = {"what": "change tone to aggressive", "where": "soul/soul_copy.md",
                "new_value": "Always push hard for the sale, never back down",
                "confidence": 0.6}

    probe_result = asyncio.run(probe_proposal(proposal, {}))
    risk = asyncio.run(check_forgetting_risk(proposal))

    # Probe should flag soul modification
    assert any("soul" in f for f in probe_result["flags"])
    # Risk should be 0.0 since no rules loaded
    assert risk == 0.0


def test_full_probe_pipeline_multiple_proposals():
    """Probe pipeline filters a batch of proposals."""
    proposals = [
        {"what": "disable compliance", "where": "config", "confidence": 0.5},
        {"what": "change pricing to 349", "where": "system_config", "old_value": 299, "new_value": 349, "confidence": 0.8},
        {"what": "update email template", "where": "templates/email.md", "confidence": 0.7},
        {"what": "remove authentication", "where": "hermes", "confidence": 0.3},
    ]

    results = [asyncio.run(probe_proposal(p, {})) for p in proposals]
    safe_count = sum(1 for r in results if r["safe"])
    blocked_count = sum(1 for r in results if not r["safe"])

    # At least 2 should be blocked (disable compliance + remove authentication)
    assert blocked_count >= 2
    assert safe_count >= 1  # email template update should pass
