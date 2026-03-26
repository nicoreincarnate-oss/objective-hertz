"""
LoRA Adapter Merging — TIES merging for multi-LoRA composition.

Papers: LoRA as Knowledge Memory, LoRAFusion.
TIES (Trim, Elect Sign, Merge) resolves conflicts when combining
multiple adapted LoRAs by trimming low-magnitude deltas and resolving
sign conflicts before merging.

Used when:
- Alpha/Beta produces multiple adapted LoRAs from different experiments
- Per-pipeline-stage LoRAs need to be combined into a single adapter
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("perseus.titan.lora_merging")


@dataclass
class MergeResult:
    output_path: Path
    source_adapters: list[str]
    merge_method: str
    metadata: dict


async def ties_merge(
    adapters: list[Path],
    weights: list[float] | None = None,
    output_dir: Path | None = None,
    trim_ratio: float = 0.2,
) -> MergeResult | None:
    """TIES merging: Trim low-magnitude → Elect sign → Merge remaining.

    1. Load delta matrices from each adapter
    2. Trim: zero out bottom `trim_ratio` of deltas by magnitude
    3. Elect: for each parameter, majority vote on sign direction
    4. Merge: weighted average of surviving deltas with elected signs

    Returns merged adapter path or None if merging fails.
    """
    if not adapters:
        return None

    if weights is None:
        weights = [1.0 / len(adapters)] * len(adapters)

    if len(weights) != len(adapters):
        logger.error("Adapter count != weight count")
        return None

    output = output_dir or adapters[0].parent / "merged_adapter"
    output.mkdir(parents=True, exist_ok=True)

    try:
        import torch

        # Load adapter state dicts
        state_dicts = []
        for adapter_path in adapters:
            sd_path = adapter_path / "adapter_model.bin"
            if not sd_path.exists():
                sd_path = adapter_path / "adapter_model.safetensors"
            if not sd_path.exists():
                logger.warning(f"No adapter weights found in {adapter_path}")
                continue
            state_dicts.append(torch.load(sd_path, map_location="cpu"))

        if len(state_dicts) < 2:
            logger.info("Need at least 2 adapters to merge")
            return None

        merged_sd = {}
        for key in state_dicts[0].keys():
            tensors = [sd[key] for sd in state_dicts if key in sd]
            if not tensors:
                continue

            # Step 1: Trim — zero out bottom trim_ratio by magnitude
            trimmed = []
            for t in tensors:
                flat = t.abs().flatten()
                threshold = torch.quantile(flat.float(), trim_ratio)
                mask = t.abs() >= threshold
                trimmed.append(t * mask)

            # Step 2: Elect sign — majority vote
            signs = torch.stack([torch.sign(t) for t in trimmed])
            elected_sign = torch.sign(signs.sum(dim=0))

            # Step 3: Merge — weighted average with elected signs
            weighted_sum = sum(w * t.abs() for w, t in zip(weights, trimmed))
            merged_sd[key] = elected_sign * weighted_sum

        # Save merged adapter
        torch.save(merged_sd, output / "adapter_model.bin")

        # Copy config from first adapter
        for cfg_file in ("adapter_config.json", "tokenizer_config.json"):
            src = adapters[0] / cfg_file
            if src.exists():
                (output / cfg_file).write_text(src.read_text())

        logger.info(f"TIES merged {len(adapters)} adapters → {output}")
        return MergeResult(
            output_path=output,
            source_adapters=[str(p) for p in adapters],
            merge_method="ties",
            metadata={"trim_ratio": trim_ratio, "weights": weights},
        )

    except ImportError:
        logger.warning("torch not available for TIES merging — skipping")
        return None
    except Exception as e:
        logger.error(f"TIES merge failed: {e}")
        return None


async def merge_stage_adapters(
    stage_adapters: dict[str, Path],
    weights: dict[str, float] | None = None,
) -> MergeResult | None:
    """Merge per-pipeline-stage LoRAs into a single adapter (LoRAFusion).

    stage_adapters: {"email_compose": Path(...), "close_deal": Path(...)}
    weights: {"email_compose": 0.6, "close_deal": 0.4} (relative importance)
    """
    if not stage_adapters:
        return None

    adapters = list(stage_adapters.values())
    if weights:
        w = [weights.get(stage, 1.0) for stage in stage_adapters.keys()]
        total = sum(w)
        w = [x / total for x in w]  # normalize
    else:
        w = None

    return await ties_merge(adapters, weights=w)
