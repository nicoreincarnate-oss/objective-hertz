"""Tests for Week 7: Bandit framework, multi-dim scoring, response latency, milestone rewards."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

os.environ["BANDIT_ENABLED"] = "1"
os.environ["MILESTONE_REWARDS_ENABLED"] = "1"

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

from shared.bandit import BanditArm, BanditPolicy, Experiment, get_bandit
from shared.milestone_rewards import (
    STAGE_REWARDS,
    compute_discounted_future_reward,
    emit_milestone_reward,
    get_transition_reward,
)


# ═══════════════════════════════════════════════════════════════
# BanditArm
# ═══════════════════════════════════════════════════════════════

def test_arm_default_mean():
    arm = BanditArm(name="test")
    assert arm.mean_reward == 0.5  # alpha=1, beta=1 → 0.5

def test_arm_sample_range():
    arm = BanditArm(name="test", alpha=5.0, beta=5.0)
    samples = [arm.sample() for _ in range(100)]
    assert all(0.0 <= s <= 1.0 for s in samples)

def test_arm_high_alpha_high_mean():
    arm = BanditArm(name="winner", alpha=100.0, beta=1.0)
    assert arm.mean_reward > 0.95


# ═══════════════════════════════════════════════════════════════
# BanditPolicy
# ═══════════════════════════════════════════════════════════════

def test_select_returns_arm_name():
    policy = BanditPolicy()
    arm = asyncio.run(policy.select("exp1", arms=["a", "b", "c"]))
    assert arm in ("a", "b", "c")

def test_select_cold_start_explores():
    """During cold start, all arms should get selected (probabilistic but very likely)."""
    policy = BanditPolicy()
    selected = set()
    for _ in range(100):
        arm = asyncio.run(policy.select("exp_cold", arms=["x", "y", "z"]))
        selected.add(arm)
    assert len(selected) >= 2  # at least 2 of 3 arms explored

def test_update_increases_alpha():
    policy = BanditPolicy()
    asyncio.run(policy.select("exp_up", arms=["a"]))
    arm_before = policy._experiments["exp_up"].arms["a"].alpha
    asyncio.run(policy.update("exp_up", "a", reward=1.0))
    arm_after = policy._experiments["exp_up"].arms["a"].alpha
    assert arm_after > arm_before

def test_update_failure_increases_beta():
    policy = BanditPolicy()
    asyncio.run(policy.select("exp_fail", arms=["a"]))
    arm_before = policy._experiments["exp_fail"].arms["a"].beta
    asyncio.run(policy.update("exp_fail", "a", reward=0.0))
    arm_after = policy._experiments["exp_fail"].arms["a"].beta
    assert arm_after > arm_before

def test_convergence_with_dominant_arm():
    """After many pulls, a clearly better arm should trigger convergence."""
    policy = BanditPolicy()
    asyncio.run(policy.select("exp_conv", arms=["winner", "loser"]))
    # Give winner 80 successes, loser 80 failures
    for _ in range(80):
        asyncio.run(policy.update("exp_conv", "winner", 1.0))
        asyncio.run(policy.update("exp_conv", "loser", 0.0))
    exp = policy._experiments["exp_conv"]
    # Should converge after 160 pulls (well past cold start of 50)
    assert exp.converged is True
    assert exp.winner == "winner"

def test_converged_returns_winner():
    """Once converged, select always returns winner."""
    policy = BanditPolicy()
    exp = Experiment(experiment_id="locked")
    exp.add_arm("best")
    exp.add_arm("worst")
    exp.converged = True
    exp.winner = "best"
    policy._experiments["locked"] = exp
    arm = asyncio.run(policy.select("locked"))
    assert arm == "best"

def test_get_stats():
    policy = BanditPolicy()
    asyncio.run(policy.select("stats_exp", arms=["a", "b"]))
    asyncio.run(policy.update("stats_exp", "a", 1.0))
    stats = policy.get_stats("stats_exp")
    assert stats["total_pulls"] == 1
    assert "a" in stats["arms"]
    assert stats["arms"]["a"]["pulls"] == 1

def test_get_stats_not_found():
    policy = BanditPolicy()
    stats = policy.get_stats("nonexistent")
    assert stats["status"] == "not_found"

def test_singleton():
    b1 = get_bandit()
    b2 = get_bandit()
    assert b1 is b2


# ═══════════════════════════════════════════════════════════════
# Milestone Rewards
# ═══════════════════════════════════════════════════════════════

def test_stage_rewards_complete():
    """All key stages have rewards defined."""
    for stage in ["discovered", "researched", "email_sent", "interested",
                  "closed", "paid", "lost"]:
        assert stage in STAGE_REWARDS

def test_paid_is_max_reward():
    assert STAGE_REWARDS["paid"] == 1.0

def test_lost_is_negative():
    assert STAGE_REWARDS["lost"] < 0

def test_get_transition_reward():
    r = get_transition_reward("email_sent", "interested")
    assert r == STAGE_REWARDS["interested"]
    assert r > 0

def test_get_transition_reward_unknown():
    r = get_transition_reward("unknown", "also_unknown")
    assert r == 0.0

def test_emit_milestone_reward():
    _fake_db.emit_event = AsyncMock(return_value=1)
    r = asyncio.run(emit_milestone_reward(42, "email_sent", "interested"))
    assert r > 0

def test_emit_milestone_lost():
    r = asyncio.run(emit_milestone_reward(42, "interested", "lost"))
    assert r < 0

def test_discounted_future_reward_early():
    """Early stages have low expected future reward."""
    r = compute_discounted_future_reward("discovered")
    assert r < 0.2

def test_discounted_future_reward_late():
    """Late stages have high expected future reward."""
    r = compute_discounted_future_reward("closed")
    assert r > 0.7

def test_discounted_future_reward_paid():
    assert compute_discounted_future_reward("paid") == 1.0
