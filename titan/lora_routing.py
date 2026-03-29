"""
LoRA Adapter Routing — Per-pipeline-stage configuration.

Papers: Poly-PRAG (shared latent routing), Spectrum (SNR selection),
Dynamic Layer-Wise TTA (per-layer adaptive rates), LoRA Critical Parameters.

Instead of one monolithic LoRA for all tasks, routes to different
configurations per pipeline stage. Email composition needs different
rank/targets than lead research or deal closing.

Gated behind LORA_ADAPTIVE_ROUTING=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.titan.lora_routing")

LORA_ADAPTIVE_ROUTING = os.environ.get("LORA_ADAPTIVE_ROUTING", "1") == "1"
LORA_SNR_SELECTION = os.environ.get("LORA_SNR_SELECTION", "1") == "1"


@dataclass
class LoRARouterConfig:
    """Configuration for a LoRA adapter tailored to a pipeline stage."""
    rank: int = 16
    lora_alpha: int = 16
    target_modules: list[str] = field(default_factory=lambda: [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ])
    layer_multipliers: dict[str, float] = field(default_factory=dict)
    use_rslora: bool = True
    dropout: float = 0.0


def default_config() -> LoRARouterConfig:
    """Current production config — rank=16, all projections, OPLoRA."""
    return LoRARouterConfig()


# Per-stage specialization based on Poly-PRAG + LoRA Critical Parameters research
_STAGE_CONFIGS: dict[str, LoRARouterConfig] = {
    "email_compose": LoRARouterConfig(
        rank=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        # Email needs full creativity — all projections, full rank
    ),
    "lead_research": LoRARouterConfig(
        rank=8,
        target_modules=["q_proj", "k_proj", "v_proj"],
        # Research is extraction — attention-only, lower rank sufficient
    ),
    "close_deal": LoRARouterConfig(
        rank=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj"],
        # Closing needs nuanced reasoning — high rank, skip down_proj
    ),
    "follow_up": LoRARouterConfig(
        rank=8,
        target_modules=["q_proj", "v_proj", "gate_proj"],
        # Follow-ups are templated — minimal adaptation needed
    ),
}


async def select_config(pipeline_stage: str) -> LoRARouterConfig:
    """Select LoRA config for a pipeline stage.

    When LORA_ADAPTIVE_ROUTING=0: returns default (current behavior).
    When LORA_ADAPTIVE_ROUTING=1: returns stage-specific config.
    """
    if not LORA_ADAPTIVE_ROUTING:
        return default_config()

    config = _STAGE_CONFIGS.get(pipeline_stage)
    if config:
        logger.debug(f"LoRA routing: {pipeline_stage} → rank={config.rank}, modules={len(config.target_modules)}")
        return config

    return default_config()


async def compute_snr_layer_scores(model_name: str) -> dict[str, float]:
    """Compute signal-to-noise ratio per layer (Spectrum paper).

    Uses Random Matrix Theory: SNR = ||W|| / expected_noise.
    High-SNR layers should get LoRA; low-SNR layers can be skipped.

    Requires MLX on Mac M4 to load model weights. Returns empty dict if unavailable.
    """
    if not LORA_SNR_SELECTION:
        return {}

    try:
        import mlx.core as mx
        from mlx_lm import load as mlx_load

        model, _ = mlx_load(model_name)
        scores = {}

        for name, param in model.items():
            if any(proj in name for proj in ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")):
                # SNR ≈ Frobenius norm / sqrt(fan_in * fan_out) (RMT baseline)
                w = mx.array(param)
                frob = float(mx.sqrt(mx.sum(w * w)))
                fan = max(1, w.shape[0] * w.shape[1]) ** 0.5
                scores[name] = frob / fan

        logger.info(f"Computed SNR for {len(scores)} layers in {model_name}")
        return scores

    except ImportError:
        logger.debug("MLX not available for SNR computation")
        return {}
    except Exception as e:
        logger.debug(f"SNR computation failed: {e}")
        return {}


def per_layer_multipliers(snr_scores: dict[str, float]) -> dict[str, float]:
    """ScaleNet-style: layers with higher SNR get higher LR multipliers.

    Normalizes SNR scores to [0.5, 2.0] range as learning rate multipliers.
    High-SNR layers learn faster; low-SNR layers are dampened.
    """
    if not snr_scores:
        return {}

    min_snr = min(snr_scores.values())
    max_snr = max(snr_scores.values())
    snr_range = max_snr - min_snr

    if snr_range < 1e-6:
        return {k: 1.0 for k in snr_scores}

    multipliers = {}
    for name, snr in snr_scores.items():
        # Normalize to [0, 1] then scale to [0.5, 2.0]
        normalized = (snr - min_snr) / snr_range
        multipliers[name] = 0.5 + 1.5 * normalized

    return multipliers
