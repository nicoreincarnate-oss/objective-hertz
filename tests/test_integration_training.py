"""Integration test: Training pipeline — muA LR + wDPO + curation."""

import os
import sys
import types
from unittest.mock import AsyncMock

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
)
_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="done"))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from titan.training import (
    compute_mua_lr,
    compute_wdpo_weights,
    curate_preference_dataset,
    uni_dpo_dynamic_weights,
)
from titan.lora_routing import (
    default_config,
    per_layer_multipliers,
    LoRARouterConfig,
)
from titan.lora_merging import MergeResult


def test_mua_lr_matches_paper():
    """muA Init[B] alpha=1: lr = 1.0 / width for attention projections."""
    lr = compute_mua_lr(4096, "q_proj")
    expected = 1.0 / 4096
    assert abs(lr - expected) < 1e-6


def test_wdpo_then_unidpo_pipeline():
    """wDPO weights feed into Uni-DPO dynamic weighting across epochs."""
    examples = [
        {"example_type": "email", "outcome": "positive"},
        {"example_type": "email", "outcome": "negative"},
        {"example_type": "email", "outcome": "positive"},
    ] * 10

    wdpo = compute_wdpo_weights(examples)
    assert len(wdpo) == 30

    # Early epoch: Uni-DPO should be near uniform
    early = uni_dpo_dynamic_weights(examples, epoch=1, total_epochs=10)
    # Late epoch: should be near wDPO
    late = uni_dpo_dynamic_weights(examples, epoch=10, total_epochs=10)

    early_variance = sum((w - 1.0)**2 for w in early) / len(early)
    late_variance = sum((w - 1.0)**2 for w in late) / len(late)
    # Late should have more variance (more differentiated) than early
    assert late_variance >= early_variance - 0.01  # small tolerance


def test_curation_then_wdpo_pipeline():
    """UltraMix curation → wDPO weights — full quality pipeline."""
    raw = [
        {"outcome": "positive", "age_days": 1, "example_type": "email"},
        {"outcome": "negative", "age_days": 1, "example_type": "email"},
        {"outcome": "positive", "age_days": 25, "example_type": "email"},
        {"outcome": "negative", "age_days": 25, "example_type": "email"},
        {"outcome": "positive", "age_days": 1, "example_type": "email"},
        {"outcome": "negative", "age_days": 25, "example_type": "email"},
    ]
    curated = curate_preference_dataset(raw)
    assert len(curated) < len(raw)  # filtered

    if curated:
        weights = compute_wdpo_weights(curated)
        assert len(weights) == len(curated)
        assert all(w > 0 for w in weights)


def test_lora_routing_config_structure():
    """LoRA config has all required fields for training script."""
    cfg = default_config()
    assert cfg.rank == 16
    assert "q_proj" in cfg.target_modules
    assert cfg.use_rslora is True
    assert cfg.lora_alpha == 16


def test_per_layer_multipliers_integration():
    """SNR scores → per-layer multipliers → training script config."""
    snr = {
        "layers.0.self_attn.q_proj": 8.5,
        "layers.0.self_attn.v_proj": 7.2,
        "layers.0.mlp.gate_proj": 3.1,
        "layers.0.mlp.up_proj": 2.8,
    }
    multipliers = per_layer_multipliers(snr)
    # Attention layers should get higher multipliers (higher SNR)
    assert multipliers["layers.0.self_attn.q_proj"] > multipliers["layers.0.mlp.up_proj"]


def test_merge_result_structure():
    """MergeResult dataclass has all fields."""
    mr = MergeResult(
        output_path=Path("/tmp/merged"),
        source_adapters=["a.bin", "b.bin"],
        merge_method="ties",
        metadata={"trim_ratio": 0.2},
    )
    assert mr.merge_method == "ties"
    assert len(mr.source_adapters) == 2
