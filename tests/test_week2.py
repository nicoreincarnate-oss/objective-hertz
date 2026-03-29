"""Tests for Week 2: Training upgrades, LoRA routing/merging, debate hardening, deep thinking."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

# ── Fake modules ──
_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_val = AsyncMock(return_value=0)

_fake_config = types.ModuleType("shared.config")
from pathlib import Path

_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    budget=types.SimpleNamespace(cloud_gpu_cap=200),
    ollama=types.SimpleNamespace(host="http://localhost:11434", model="qwen2.5:14b"),
    conway=types.SimpleNamespace(enabled=False, api_url="", api_key=""),
    ruflo=types.SimpleNamespace(enabled=False),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="http://localhost:8888", qdrant_host="", qdrant_collection="",
                                 zep_url="", zep_enabled=False),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="test output"))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)
_fake_comms.store_learning = AsyncMock()
_fake_comms.broadcast = AsyncMock()

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from perseus.sleep_cycle import _proposal_similarity
from shared.deep_thinking import estimate_thinking_depth
from titan.lora_routing import (
    default_config,
    per_layer_multipliers,
    select_config,
)
from titan.training import (
    compute_mua_lr,
    compute_wdpo_weights,
    curate_preference_dataset,
    uni_dpo_dynamic_weights,
)

# ═══════════════════════════════════════════════════════════════
# muA Learning Rates
# ═══════════════════════════════════════════════════════════════

def test_mua_lr_attention():
    lr = compute_mua_lr(4096, "q_proj")
    assert 0.0001 < lr < 0.001  # ~2.4e-4 for width=4096

def test_mua_lr_ffn():
    lr = compute_mua_lr(4096, "gate_proj")
    assert lr < compute_mua_lr(4096, "q_proj")  # FFN gets lower alpha

def test_mua_lr_scales_with_width():
    lr_small = compute_mua_lr(1024, "q_proj")
    lr_large = compute_mua_lr(4096, "q_proj")
    assert lr_small > lr_large  # smaller model → higher LR

def test_mua_lr_zero_width():
    lr = compute_mua_lr(0, "q_proj")
    assert lr == 1.0  # max(1, 0) = 1, alpha/1 = 1.0


# ═══════════════════════════════════════════════════════════════
# wDPO Noise Robustness
# ═══════════════════════════════════════════════════════════════

def test_wdpo_empty():
    assert compute_wdpo_weights([]) == []

def test_wdpo_single_type():
    examples = [
        {"example_type": "email", "outcome": "positive"},
        {"example_type": "email", "outcome": "positive"},
        {"example_type": "email", "outcome": "negative"},
    ]
    weights = compute_wdpo_weights(examples)
    assert len(weights) == 3
    assert all(0.0 < w <= 1.0 for w in weights)

def test_wdpo_noisy_type_lower_weight():
    """Types with near-50/50 split should get lower weights (noisier)."""
    noisy = [
        {"example_type": "noisy", "outcome": "positive"},
        {"example_type": "noisy", "outcome": "negative"},
    ]
    clean = [
        {"example_type": "clean", "outcome": "positive"},
        {"example_type": "clean", "outcome": "positive"},
        {"example_type": "clean", "outcome": "positive"},
        {"example_type": "clean", "outcome": "negative"},
    ]
    noisy_w = compute_wdpo_weights(noisy)
    clean_w = compute_wdpo_weights(clean)
    # Noisy type has 50/50 split → lower weight
    assert noisy_w[0] <= clean_w[0]


# ═══════════════════════════════════════════════════════════════
# Uni-DPO Dynamic Weights
# ═══════════════════════════════════════════════════════════════

def test_uni_dpo_early_epoch_is_uniform():
    examples = [{"example_type": "email", "outcome": "positive"}] * 5
    weights = uni_dpo_dynamic_weights(examples, epoch=0, total_epochs=10)
    # Early epoch: should be close to uniform (1.0)
    assert all(abs(w - 1.0) < 0.1 for w in weights)

def test_uni_dpo_late_epoch_matches_wdpo():
    examples = [
        {"example_type": "email", "outcome": "positive"},
        {"example_type": "email", "outcome": "negative"},
    ]
    weights = uni_dpo_dynamic_weights(examples, epoch=10, total_epochs=10)
    wdpo = compute_wdpo_weights(examples)
    # Late epoch: should be close to wDPO weights
    for u, w in zip(weights, wdpo):
        assert abs(u - w) < 0.15


# ═══════════════════════════════════════════════════════════════
# UltraMix Data Curation
# ═══════════════════════════════════════════════════════════════

def test_curate_empty():
    assert curate_preference_dataset([]) == []

def test_curate_filters_bottom_30():
    examples = [{"outcome": "positive", "age_days": 1}] * 10
    curated = curate_preference_dataset(examples)
    assert len(curated) <= 7  # 70% of 10

def test_curate_recent_preferred():
    examples = [
        {"outcome": "positive", "age_days": 1},  # recent → high score
        {"outcome": "positive", "age_days": 25},  # old → lower score
        {"outcome": "negative", "age_days": 1},
        {"outcome": "negative", "age_days": 25},
    ]
    curated = curate_preference_dataset(examples)
    assert len(curated) > 0


# ═══════════════════════════════════════════════════════════════
# LoRA Routing
# ═══════════════════════════════════════════════════════════════

def test_default_config_rank():
    cfg = default_config()
    assert cfg.rank == 16
    assert cfg.use_rslora is True

def test_select_config_disabled():
    """When LORA_ADAPTIVE_ROUTING=0, returns default."""
    cfg = asyncio.run(select_config("email_compose"))
    assert cfg.rank == 16  # default

def test_per_layer_multipliers_uniform():
    scores = {"layer1": 1.0, "layer2": 1.0, "layer3": 1.0}
    multipliers = per_layer_multipliers(scores)
    assert all(abs(m - 1.0) < 0.01 for m in multipliers.values())

def test_per_layer_multipliers_varied():
    scores = {"high_snr": 10.0, "low_snr": 1.0}
    multipliers = per_layer_multipliers(scores)
    assert multipliers["high_snr"] > multipliers["low_snr"]
    assert 0.5 <= multipliers["low_snr"] <= 2.0
    assert 0.5 <= multipliers["high_snr"] <= 2.0

def test_per_layer_multipliers_empty():
    assert per_layer_multipliers({}) == {}


# ═══════════════════════════════════════════════════════════════
# Debate Hardening — Proposal Dedup
# ═══════════════════════════════════════════════════════════════

def test_proposal_similarity_exact():
    a = {"what": "change pricing to 349", "where": "system_config"}
    b = {"what": "change pricing to 349", "where": "system_config"}
    assert _proposal_similarity(a, b) > 0.9

def test_proposal_similarity_different():
    a = {"what": "change pricing to 349", "where": "system_config"}
    b = {"what": "update email template tone", "where": "soul_copy.md"}
    assert _proposal_similarity(a, b) < 0.3

def test_proposal_similarity_partial():
    a = {"what": "change pricing from 299 to 349", "where": "system_config"}
    b = {"what": "change pricing from 299 to 399", "where": "system_config"}
    assert 0.5 < _proposal_similarity(a, b) < 1.0

def test_proposal_similarity_empty():
    assert _proposal_similarity({}, {}) == 0.0


# ═══════════════════════════════════════════════════════════════
# Deep-Thinking Tokens
# ═══════════════════════════════════════════════════════════════

def test_thinking_depth_shallow():
    """Generic text with no reasoning structure → low depth."""
    text = "Hello world this is a test message."
    assert estimate_thinking_depth(text) < 0.4

def test_thinking_depth_deep_reasoning():
    """Text with reasoning, evidence, and structure → high depth."""
    text = (
        "Based on the data from last week's campaign metrics, the reply rate was 2.3%. "
        "Because dentists in Austin respond better to Tuesday morning sends, "
        "therefore we should shift the sending schedule. "
        "However, this contradicts the previous finding about Thursday afternoons. "
        "1. Check the sample size for each day. "
        "2. Run an A/B test for 2 weeks. "
        "3. Compare reply rates with statistical significance."
    )
    assert estimate_thinking_depth(text) >= 0.5

def test_thinking_depth_empty():
    assert estimate_thinking_depth("") == 0.0

def test_thinking_depth_very_short():
    assert estimate_thinking_depth("Yes.") < 0.2

def test_think_at_n_disabled():
    """When disabled, generates once without scoring."""
    os.environ["DEEP_THINKING_ENABLED"] = "0"
    # Re-import to pick up env var
    from shared.deep_thinking import think_at_n
    gen_fn = AsyncMock(return_value="simple output")
    result = asyncio.run(think_at_n(gen_fn, "test prompt"))
    assert result["candidates_tried"] == 1
    assert result["text"] == "simple output"
