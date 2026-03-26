"""
Hybrid RAG — Combine parametric (LoRA) + token (Qdrant) + explicit (Neo4j).

Papers: Understanding Parametric Knowledge Injection (PT-RAG beats both alone),
P-RAG (LoRA + retrieval), LoRA as Knowledge Memory (validates hybrid),
RLVRR (reward chains beat 10x SFT data).

The key insight: LoRA alone is NOT always better than RAG. But LoRA + RAG
together beats both. This merges 3 knowledge sources simultaneously:
1. Parametric: LoRA adapter forward pass (implicit knowledge in weights)
2. Token-based: Qdrant vector search (explicit retrieved context)
3. Graph-based: Neo4j MAGMA traversal (causal/structural knowledge)

Gated behind HYBRID_RAG=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.hybrid_rag")

HYBRID_RAG = os.environ.get("HYBRID_RAG", "1") == "1"
REWARD_CHAINS = os.environ.get("REWARD_CHAINS", "1") == "1"


async def hybrid_retrieve(query: str, limit: int = 10) -> list[dict]:
    """Merge 3 knowledge sources: parametric + token + graph.

    Each source returns scored results. Results are interleaved by score.
    Fused results carry provenance tags for the linearizer.
    """
    if not HYBRID_RAG:
        # Fallback: use MAGMA which already merges Qdrant + Neo4j
        try:
            from shared.magma import magma_retrieve
            text = await magma_retrieve(query, limit=limit)
            return [{"content": text, "source": "magma", "score": 1.0}]
        except Exception:
            return []

    results = []

    # Source 1: Token-based (Qdrant via Mem0)
    try:
        from shared.magma import _search_memory_with_metadata
        qdrant_results = await _search_memory_with_metadata(query, limit=limit)
        for r in qdrant_results:
            results.append({
                "content": r.get("content", ""),
                "source": "qdrant",
                "provenance": "token_rag",
                "score": r.get("score", 0.5),
                "category": r.get("category", ""),
            })
    except Exception:
        pass

    # Source 2: Graph-based (Neo4j MAGMA traversal)
    try:
        from shared.magma import magma_retrieve, _get_driver
        if _get_driver():
            graph_text = await magma_retrieve(query, limit=limit // 2)
            if graph_text and "No relevant" not in graph_text:
                results.append({
                    "content": graph_text,
                    "source": "neo4j",
                    "provenance": "graph_rag",
                    "score": 0.8,
                })
    except Exception:
        pass

    # Source 3: Parametric (LoRA adapter — query the local model directly)
    try:
        from shared.llm_client import llm
        # Ask the model what it "knows" about this topic from its weights
        parametric = await llm.generate(
            f"Based on your training data (not external sources), what do you know about: {query[:200]}",
            model="local",
            max_tokens=300,
        )
        if parametric and len(parametric.strip()) > 20:
            results.append({
                "content": parametric,
                "source": "parametric",
                "provenance": "lora_weights",
                "score": 0.6,  # Lower confidence — model hallucination risk
            })
    except Exception:
        pass

    # Source 4: Structured learnings (Postgres titan_rules + titan_learnings)
    try:
        from shared.db import fetch_all
        rules = await fetch_all(
            """SELECT rule_text, confidence FROM titan_rules
               WHERE active = TRUE AND confidence > 0.6
               ORDER BY confidence DESC LIMIT 5"""
        )
        for rule in rules:
            results.append({
                "content": rule.get("rule_text", ""),
                "source": "postgres",
                "provenance": "titan_rules",
                "score": rule.get("confidence", 0.7),
            })
    except Exception:
        pass

    # Sort by score, interleave sources for diversity
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def reward_chain_score(interaction_history: list[dict]) -> float:
    """RLVRR: compute reward chains from sequential interactions.

    Instead of per-step rewards, chains reward sequences that led to
    good outcomes. A chain is: [action_1, action_2, ..., outcome].
    The entire chain gets credited, not just the final action.

    Chains beat 10x SFT data for training signal quality.

    Returns: composite reward score 0.0-1.0 for the interaction chain.
    """
    if not REWARD_CHAINS or not interaction_history:
        return 0.5  # neutral

    # Score each step based on eventual outcome
    final_outcome = interaction_history[-1].get("outcome", "")
    if final_outcome == "positive":
        base_reward = 1.0
    elif final_outcome == "negative":
        base_reward = 0.0
    else:
        base_reward = 0.5

    # Discount earlier steps (closer to outcome = more credit)
    total_steps = len(interaction_history)
    chain_score = 0.0
    for i, step in enumerate(interaction_history):
        # Temporal discount: step closer to outcome gets more credit
        discount = 0.5 + 0.5 * (i / max(1, total_steps - 1))
        step_reward = base_reward * discount

        # Adjust by step-specific quality signals
        if step.get("reply_received"):
            step_reward *= 1.2  # engagement signal
        if step.get("bounce"):
            step_reward *= 0.5  # negative signal

        chain_score += step_reward

    # Normalize to 0.0-1.0
    return min(1.0, chain_score / max(1, total_steps))
