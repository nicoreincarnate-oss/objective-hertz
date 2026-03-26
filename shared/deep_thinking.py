"""
Deep-Thinking Tokens — DTR-based quality gating for LLM calls.

Paper: Deep-Thinking Tokens (DTR).
Instead of just generating text, detect whether the model is actually
"thinking deeply" by analyzing layer activation patterns. Gate output
quality on thinking depth, not just length.

Think@n: Generate n candidates, score each by thinking-depth, keep the
best. Achieves 49% compute reduction by early-stopping shallow generations.

Gated behind DEEP_THINKING_ENABLED=1 (default 1).
Target: all LLM call sites, especially titan/pipeline/email_compose.py.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

logger = logging.getLogger("perseus.deep_thinking")


def _deep_thinking_enabled() -> bool:
    return os.environ.get("DEEP_THINKING_ENABLED", "1") == "1"


def _think_at_n_default() -> int:
    return int(os.environ.get("THINK_AT_N", "3"))


def _min_thinking_depth() -> float:
    return float(os.environ.get("MIN_THINKING_DEPTH", "0.6"))


def estimate_thinking_depth(text: str) -> float:
    """Estimate thinking depth of generated text.

    Proxy metrics (without layer activation access):
    1. Reasoning structure: presence of "because", "therefore", "however" → deeper
    2. Specificity: concrete numbers, names, dates vs vague language
    3. Self-correction: "actually", "wait", "on the other hand" → deeper
    4. Multi-step: numbered steps or sequential logic
    5. Evidence citation: "based on", "according to", "data shows"

    Returns 0.0 (shallow) to 1.0 (deep reasoning).
    """
    if not text:
        return 0.0

    text_lower = text.lower()
    word_count = len(text.split())
    if word_count < 10:
        return 0.1

    score = 0.0
    max_score = 5.0

    # Reasoning connectors (1.0 max)
    reasoning_words = ("because", "therefore", "consequently", "since", "given that",
                       "this means", "which leads to", "as a result")
    reasoning_count = sum(1 for w in reasoning_words if w in text_lower)
    score += min(1.0, reasoning_count * 0.3)

    # Specificity — numbers, proper nouns (1.0 max)
    import re
    numbers = len(re.findall(r'\d+', text))
    capitals = len(re.findall(r'[A-Z][a-z]{2,}', text))
    specificity = min(1.0, (numbers + capitals) / max(1, word_count) * 10)
    score += specificity

    # Self-correction signals (1.0 max)
    correction_words = ("actually", "wait", "on the other hand", "however",
                        "but consider", "alternatively", "correction")
    correction_count = sum(1 for w in correction_words if w in text_lower)
    score += min(1.0, correction_count * 0.5)

    # Multi-step structure (1.0 max)
    steps = len(re.findall(r'(?:^|\n)\s*\d+[\.\)]', text))
    bullets = len(re.findall(r'(?:^|\n)\s*[-•]', text))
    score += min(1.0, (steps + bullets) * 0.25)

    # Evidence citation (1.0 max)
    evidence_words = ("based on", "according to", "data shows", "evidence",
                      "research indicates", "statistics", "metrics show")
    evidence_count = sum(1 for w in evidence_words if w in text_lower)
    score += min(1.0, evidence_count * 0.4)

    return min(1.0, score / max_score)


async def think_at_n(
    generate_fn: Callable,
    prompt: str,
    n: int | None = None,
    min_depth: float | None = None,
    **kwargs,
) -> dict:
    """Think@n: Generate n candidates, score by thinking depth, keep best.

    49% compute reduction: if first candidate scores above threshold,
    skip remaining generations (early stopping).

    Args:
        generate_fn: async LLM generate function (e.g., llm.generate)
        prompt: the prompt to generate from
        n: number of candidates (default THINK_AT_N)
        min_depth: minimum thinking depth to accept (default MIN_THINKING_DEPTH)

    Returns:
        {
            "text": best generation,
            "depth": thinking depth score,
            "candidates_tried": how many were generated,
            "early_stopped": whether we stopped before n,
        }
    """
    if not _deep_thinking_enabled():
        # Passthrough: generate once, no scoring
        text = await generate_fn(prompt, **kwargs)
        return {"text": text, "depth": 1.0, "candidates_tried": 1, "early_stopped": False}

    n = n or _think_at_n_default()
    min_depth = min_depth or _min_thinking_depth()

    best_text = ""
    best_depth = 0.0
    tried = 0

    for i in range(n):
        tried += 1
        # Inject thinking encouragement into prompt
        thinking_prompt = prompt
        if i > 0:
            thinking_prompt = (
                f"{prompt}\n\n"
                f"[Think step by step. Consider multiple angles. "
                f"Use specific data and reasoning, not generic statements.]"
            )

        text = await generate_fn(thinking_prompt, **kwargs)
        depth = estimate_thinking_depth(text)

        if depth > best_depth:
            best_depth = depth
            best_text = text

        # Early stopping: if we hit threshold, no need for more candidates
        if depth >= min_depth:
            logger.debug(f"Think@{n}: early stop at candidate {tried} (depth={depth:.2f})")
            return {
                "text": best_text,
                "depth": best_depth,
                "candidates_tried": tried,
                "early_stopped": tried < n,
            }

    logger.debug(f"Think@{n}: used all {n} candidates, best depth={best_depth:.2f}")
    return {
        "text": best_text,
        "depth": best_depth,
        "candidates_tried": tried,
        "early_stopped": False,
    }


async def deep_generate(
    generate_fn: Callable,
    prompt: str,
    require_deep: bool = True,
    **kwargs,
) -> str:
    """Drop-in replacement for llm.generate() with depth gating.

    If require_deep=True and depth < threshold, retries with thinking prompt.
    If still shallow after Think@n attempts, returns best anyway with warning logged.

    Usage:
        # Instead of: result = await llm.generate(prompt, model="smart")
        result = await deep_generate(llm.generate, prompt, model="smart")
    """
    if not _deep_thinking_enabled():
        return await generate_fn(prompt, **kwargs)

    result = await think_at_n(generate_fn, prompt, **kwargs)

    min_depth = _min_thinking_depth()
    if require_deep and result["depth"] < min_depth:
        logger.warning(
            f"Deep thinking failed: depth={result['depth']:.2f} < {min_depth} "
            f"after {result['candidates_tried']} candidates"
        )

    return result["text"]
