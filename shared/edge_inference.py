"""Edge inference background job for the MAGMA brain graph.

Phase 42 — Agent A5.

Periodically scans recent MAGMA nodes and infers edges between them based on:
    - Embedding cosine similarity   -> RELATED_TO
    - Temporal proximity (<60s)     -> CAUSED_BY (directional, older -> newer)
    - Shared mentioned entities     -> MENTIONS (substring overlap heuristic)

The job is idempotent: existing edges between two nodes (any direction, any
type) are skipped. A per-node cap (`max_pairs_per_node`) prevents runaway
densification of hub nodes.

This module deliberately depends ONLY on `shared.db` so that it can be unit
tested in isolation against a mocked connection pool. It does not import
`shared.magma` to avoid coupling and to keep the file scope narrow.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from shared import db

logger = logging.getLogger(__name__)

# Edge type constants — kept local to avoid importing shared.magma_edge_types
# (which is owned by Agent A6 in Phase 42 and may not exist yet).
EDGE_RELATED_TO = "RELATED_TO"
EDGE_MENTIONS = "MENTIONS"
EDGE_CAUSED_BY = "CAUSED_BY"

# Temporal window for CAUSED_BY inference (seconds).
CAUSED_BY_WINDOW_SECONDS = 60.0
# Lower similarity floor for CAUSED_BY (less strict than RELATED_TO).
CAUSED_BY_MIN_SIMILARITY = 0.5


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity, no numpy required.

    Returns 0.0 for mismatched lengths or zero-norm vectors so callers do not
    need to special-case those situations.
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _extract_entities(text: str | None) -> set[str]:
    """Naive entity extractor: lowercase tokens >= 4 chars.

    Real entity extraction lives in `shared/magma.py`; this is intentionally a
    cheap fallback used purely for the MENTIONS overlap heuristic.
    """
    if not text:
        return set()
    tokens = {tok.strip(".,;:!?()[]{}\"'").lower() for tok in text.split()}
    return {t for t in tokens if len(t) >= 4}


def _shared_entities(node_a: dict[str, Any], node_b: dict[str, Any]) -> bool:
    """Return True if nodes A and B share at least one entity token."""
    a_entities = _extract_entities(node_a.get("content"))
    b_entities = _extract_entities(node_b.get("content"))
    if not a_entities or not b_entities:
        return False
    return bool(a_entities & b_entities)


def _temporal_delta_seconds(node_a: dict[str, Any], node_b: dict[str, Any]) -> float | None:
    """Absolute seconds between two node `created_at` timestamps."""
    ts_a = node_a.get("created_at")
    ts_b = node_b.get("created_at")
    if ts_a is None or ts_b is None:
        return None
    try:
        return abs((ts_b - ts_a).total_seconds())
    except (AttributeError, TypeError):
        return None


async def _edge_exists(node_id_a: str, node_id_b: str) -> bool:
    """Return True if any edge exists between A and B (any direction/type)."""
    row = await db.fetch_one(
        """
        SELECT 1 AS present
        FROM magma_edges
        WHERE (source_id = %s AND target_id = %s)
           OR (source_id = %s AND target_id = %s)
        LIMIT 1
        """,
        (node_id_a, node_id_b, node_id_b, node_id_a),
    )
    return row is not None


async def _insert_edge(
    source_id: str,
    target_id: str,
    edge_type: str,
    weight: float,
) -> None:
    """Insert a single edge row. Caller is responsible for idempotency check."""
    await db.execute(
        """
        INSERT INTO magma_edges (source_id, target_id, edge_type, weight, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        """,
        (source_id, target_id, edge_type, float(weight)),
    )


async def infer_edges_for_recent_nodes(
    lookback_hours: int = 24,
    similarity_threshold: float = 0.7,
    max_pairs_per_node: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan recent MAGMA nodes and infer edges between them.

    Edge types written:
        - RELATED_TO (semantic similarity > threshold)
        - MENTIONS   (extracted entity overlap, simple substring match for now)
        - CAUSED_BY  (temporal proximity within 60 seconds + similarity > 0.5)

    Args:
        lookback_hours: How far back to scan for candidate nodes.
        similarity_threshold: Cosine similarity floor for RELATED_TO edges.
        max_pairs_per_node: Cap on new edges per source node A (densification
            guard for hub nodes).
        dry_run: If True, compute everything but issue no INSERTs.

    Returns:
        Summary dict with `nodes_examined`, `edges_inferred`,
        `edges_skipped_existing`, and `errors`.
    """
    stats: dict[str, Any] = {
        "nodes_examined": 0,
        "edges_inferred": 0,
        "edges_skipped_existing": 0,
        "errors": [],
    }

    try:
        nodes = await db.fetch_all(
            """
            SELECT id, embedding, created_at, content
            FROM magma_nodes
            WHERE created_at >= NOW() - (%s || ' hours')::interval
            ORDER BY created_at ASC
            """,
            (str(lookback_hours),),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("edge_inference: failed to fetch recent nodes")
        stats["errors"].append(f"fetch_recent_nodes: {exc}")
        return stats

    stats["nodes_examined"] = len(nodes)
    if not nodes:
        return stats

    for idx, node_a in enumerate(nodes):
        new_edges_for_a = 0
        for node_b in nodes[idx + 1 :]:
            if new_edges_for_a >= max_pairs_per_node:
                break

            if node_a.get("id") == node_b.get("id"):
                continue

            emb_a = node_a.get("embedding")
            emb_b = node_b.get("embedding")
            if emb_a is None or emb_b is None:
                continue

            try:
                similarity = cosine_similarity(list(emb_a), list(emb_b))
            except Exception as exc:
                stats["errors"].append(
                    f"cosine({node_a.get('id')},{node_b.get('id')}): {exc}"
                )
                continue

            wants_related = similarity > similarity_threshold
            delta = _temporal_delta_seconds(node_a, node_b)
            wants_causal = (
                delta is not None
                and delta <= CAUSED_BY_WINDOW_SECONDS
                and similarity > CAUSED_BY_MIN_SIMILARITY
            )
            wants_mentions = _shared_entities(node_a, node_b)

            if not (wants_related or wants_causal or wants_mentions):
                continue

            try:
                if await _edge_exists(node_a["id"], node_b["id"]):
                    stats["edges_skipped_existing"] += 1
                    continue
            except Exception as exc:
                stats["errors"].append(
                    f"edge_exists({node_a.get('id')},{node_b.get('id')}): {exc}"
                )
                continue

            edges_to_write: list[tuple[str, str, str, float]] = []
            if wants_related:
                edges_to_write.append(
                    (node_a["id"], node_b["id"], EDGE_RELATED_TO, similarity)
                )
            if wants_causal:
                # Direction: older -> newer.
                ts_a = node_a.get("created_at")
                ts_b = node_b.get("created_at")
                if ts_a is not None and ts_b is not None and ts_b >= ts_a:
                    src, dst = node_a["id"], node_b["id"]
                else:
                    src, dst = node_b["id"], node_a["id"]
                edges_to_write.append((src, dst, EDGE_CAUSED_BY, similarity))
            if wants_mentions:
                edges_to_write.append(
                    (node_a["id"], node_b["id"], EDGE_MENTIONS, 1.0)
                )

            for src, dst, edge_type, weight in edges_to_write:
                if dry_run:
                    stats["edges_inferred"] += 1
                    new_edges_for_a += 1
                    continue
                try:
                    await _insert_edge(src, dst, edge_type, weight)
                    stats["edges_inferred"] += 1
                    new_edges_for_a += 1
                except Exception as exc:
                    stats["errors"].append(
                        f"insert_edge({src},{dst},{edge_type}): {exc}"
                    )

            if new_edges_for_a >= max_pairs_per_node:
                break

    logger.info(
        "edge_inference complete: examined=%d inferred=%d skipped=%d errors=%d",
        stats["nodes_examined"],
        stats["edges_inferred"],
        stats["edges_skipped_existing"],
        len(stats["errors"]),
    )
    return stats
