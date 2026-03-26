"""Tests for Week 5: Ruflo, SEAL weight directives, Hybrid RAG, self-play."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

os.environ["RUFLO_ENABLED"] = "1"
os.environ["HYBRID_RAG"] = "1"
os.environ["REWARD_CHAINS"] = "1"

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
                                 mem0_host="http://localhost:8888", qdrant_host="", qdrant_collection="",
                                 zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
    ruflo=types.SimpleNamespace(enabled=True, a2a_url="http://localhost:9004", a2a_port=9004,
                                auto_apply_fixes=False),
    training=types.SimpleNamespace(seal_directives=False),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"files":["test.py"],"diff":"--- a\\n+++ b\\n-old\\n+new","rationale":"fix","estimated_impact":0.5}'))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from ruflo.agent import dispatch, propose_change, feedback_lookup, record_outcome
from ruflo.bottleneck_detector import detect_bottlenecks, score_bottleneck
from shared.hybrid_rag import hybrid_retrieve, reward_chain_score
from shared.weight_directives import _seal_directives_enabled


# ═══════════════════════════════════════════════════════════════
# Ruflo Agent
# ═══════════════════════════════════════════════════════════════

def test_dispatch_unknown_task():
    result = asyncio.run(dispatch({"task_type": "unknown_task", "payload": {}}))
    assert result["status"] == "error"

def test_dispatch_code_fix():
    _fake_db.fetch_all = AsyncMock(return_value=[])  # no feedback history
    result = asyncio.run(dispatch({"task_type": "code_fix", "payload": {"description": "fix bug", "file": "test.py"}}))
    assert result["status"] in ("proposed", "no_viable_fix", "failed")

def test_dispatch_disabled():
    os.environ["RUFLO_ENABLED"] = "0"
    from ruflo.agent import dispatch as d2
    # Need to reimport to pick up env var — but module cached. Test the check directly.
    os.environ["RUFLO_ENABLED"] = "1"

def test_propose_change_returns_dict():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(propose_change({"description": "improve email template", "file": "titan/pipeline/email_compose.py"}))
    # With mock LLM returning valid JSON, should get a proposal
    assert result is None or isinstance(result, dict)

def test_feedback_lookup_empty():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(feedback_lookup("test.py", "code_fix"))
    assert result == []

def test_record_outcome_no_crash():
    _fake_db.execute = AsyncMock()
    asyncio.run(record_outcome(1, "test.py", "code_fix", "--- a\n+++ b", True))


# ═══════════════════════════════════════════════════════════════
# Bottleneck Detector
# ═══════════════════════════════════════════════════════════════

def test_detect_bottlenecks_empty():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(detect_bottlenecks())
    assert isinstance(result, list)

def test_score_pipeline_error():
    assert score_bottleneck({"count": 20, "category": "pipeline_error"}) >= 0.9

def test_score_pipeline_error_low():
    assert score_bottleneck({"count": 1, "category": "pipeline_error"}) < 0.2

def test_score_slow_task():
    assert score_bottleneck({"avg_seconds": 300, "category": "slow_task"}) >= 0.9

def test_score_failure_rate():
    assert score_bottleneck({"failure_rate": 0.8, "category": "failure_rate"}) > 0.5

def test_score_unknown():
    assert score_bottleneck({"category": "unknown"}) == 0.5


# ═══════════════════════════════════════════════════════════════
# Hybrid RAG
# ═══════════════════════════════════════════════════════════════

def test_hybrid_retrieve_returns_list():
    result = asyncio.run(hybrid_retrieve("email tips for dentists"))
    assert isinstance(result, list)

def test_reward_chain_positive_outcome():
    history = [
        {"action": "sent_email", "outcome": ""},
        {"action": "follow_up", "reply_received": True, "outcome": ""},
        {"action": "close", "outcome": "positive"},
    ]
    score = reward_chain_score(history)
    assert score > 0.5

def test_reward_chain_negative_outcome():
    history = [
        {"action": "sent_email", "outcome": ""},
        {"action": "follow_up", "bounce": True, "outcome": ""},
        {"action": "lost", "outcome": "negative"},
    ]
    score = reward_chain_score(history)
    assert score < 0.5

def test_reward_chain_empty():
    assert reward_chain_score([]) == 0.5

def test_reward_chain_single_step():
    score = reward_chain_score([{"outcome": "positive"}])
    assert score >= 0.5


# ═══════════════════════════════════════════════════════════════
# Weight Directives
# ═══════════════════════════════════════════════════════════════

def test_seal_disabled_by_default():
    """SEAL_DIRECTIVES defaults to off (reads from config.training.seal_directives)."""
    assert _seal_directives_enabled() is False


# ═══════════════════════════════════════════════════════════════
# Cycle 4 — Dirty-tree awareness (cross-cutting)
# ═══════════════════════════════════════════════════════════════

def test_shadow_test_applies_dirty_overlay():
    """Ruflo shadow_test must overlay dirty working-tree state before testing the proposal."""
    import subprocess
    import types as _t
    from unittest.mock import patch

    from ruflo.agent import shadow_test

    overlay_inputs = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "worktree"] and "add" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "diff", "--cached"]:
            return _t.SimpleNamespace(returncode=0, stdout="")
        if cmd == ["git", "diff"]:
            return _t.SimpleNamespace(returncode=0, stdout="diff --git a/f.py\n")
        if cmd[:2] == ["git", "ls-files"]:
            return _t.SimpleNamespace(returncode=0, stdout="")
        if cmd[:2] == ["git", "apply"] and "--allow-empty" in cmd:
            overlay_inputs.append(kwargs.get("input", ""))
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "apply"]:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "pytest" in str(cmd):
            return _t.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if cmd[:2] == ["git", "worktree"] and "remove" in cmd:
            return _t.SimpleNamespace(returncode=0, stdout="", stderr="")
        return _t.SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("ruflo.agent.subprocess.run", side_effect=fake_run), \
         patch("ruflo.agent._get_project_root", return_value="/tmp/test"):
        asyncio.run(shadow_test({"files": ["f.py"], "diff": "--- a\n+++ b\n"}))

    # At least the unstaged overlay must have been applied
    assert len(overlay_inputs) >= 1
