"""
Inference Optimizer — KV cache management + compute routing.

Papers: Mamba-3 (linear-time inference), Chelsea (40-50% KV cache reduction),
Learning When to Plan (adaptive compute allocation), Efficient Attention (compression).

Capabilities:
1. Estimate KV cache usage before sending a prompt
2. Compress context by clustering similar memories
3. Route to efficient vs full-attention mode based on task complexity

Gated behind INFERENCE_OPTIMIZATION=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.inference_optimizer")

INFERENCE_OPTIMIZATION = os.environ.get("INFERENCE_OPTIMIZATION", "1") == "1"

# Approximate tokens per char for English text
TOKENS_PER_CHAR = 0.25
# KV cache bytes per token per layer (rough estimate for Qwen2.5-14B)
KV_BYTES_PER_TOKEN = 256  # ~256 bytes/token for 14B model with 40 layers


def estimate_kv_cache_usage(prompt_tokens: int, model: str = "qwen2.5:14b") -> int:
    """Estimate KV cache memory in bytes for a given prompt.

    Chelsea paper: KV cache is the primary memory bottleneck.
    For Qwen2.5-14B: ~256 bytes per token across all layers.
    """
    return prompt_tokens * KV_BYTES_PER_TOKEN


def should_compress_context(prompt_text: str, budget_mb: float = 512.0) -> bool:
    """Check if prompt would exceed KV cache memory budget.

    Default budget: 512MB (conservative for M4 32GB with other processes).
    """
    if not INFERENCE_OPTIMIZATION:
        return False

    tokens = int(len(prompt_text) * TOKENS_PER_CHAR)
    usage_bytes = estimate_kv_cache_usage(tokens)
    budget_bytes = budget_mb * 1024 * 1024

    return usage_bytes > budget_bytes


def compress_context(memories: list[str], budget_tokens: int = 2000) -> list[str]:
    """Compress memories by deduplication and truncation.

    Chelsea paper: cluster similar memories, keep representative from each cluster.
    Simple implementation: deduplicate by prefix, truncate longest, keep within budget.

    Returns compressed list fitting within budget.
    """
    if not memories:
        return []

    # Deduplicate by first 50 chars
    seen_prefixes: set[str] = set()
    unique: list[str] = []
    for mem in memories:
        prefix = mem[:50].lower().strip()
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            unique.append(mem)

    # Sort by length (shorter = more dense information)
    unique.sort(key=len)

    # Fill budget
    result = []
    tokens_used = 0
    for mem in unique:
        mem_tokens = int(len(mem) * TOKENS_PER_CHAR)
        if tokens_used + mem_tokens > budget_tokens:
            # Truncate this memory to fit remaining budget
            remaining_tokens = budget_tokens - tokens_used
            remaining_chars = int(remaining_tokens / TOKENS_PER_CHAR)
            if remaining_chars > 50:
                result.append(mem[:remaining_chars] + "...")
            break
        result.append(mem)
        tokens_used += mem_tokens

    return result


def select_inference_mode(
    task_complexity: str = "medium",
    prompt_tokens: int = 0,
    budget_remaining_pct: float = 1.0,
) -> str:
    """Select inference mode based on task complexity and budget.

    Mamba-3 paper: linear-time inference for long conversations.
    Learning When to Plan: allocate more compute to complex tasks.

    Returns: "standard" | "efficient" | "cached"
    - standard: full attention, best quality (for complex tasks)
    - efficient: reduced context, faster (for simple tasks)
    - cached: use cached/memoized result if available (for repeated queries)
    """
    if not INFERENCE_OPTIMIZATION:
        return "standard"

    # Budget pressure → efficient mode
    if budget_remaining_pct < 0.2:
        return "efficient"

    # Long prompts → efficient mode (Mamba-3: linear-time better for long sequences)
    if prompt_tokens > 4000:
        return "efficient"

    # Simple tasks don't need full attention
    if task_complexity in ("simple", "classification", "extraction"):
        return "efficient"

    # Complex tasks get full compute
    if task_complexity in ("complex", "reasoning", "composition", "strategy"):
        return "standard"

    return "standard"
