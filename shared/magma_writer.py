"""High-level runtime hook for writing daemon events into the MAGMA brain.

Phase 42 — Stage B (event → memory hook).

This module is the runtime entry point that any daemon (Perseus, Titan, Hermes,
ClawdBot, Conway, etc.) calls when it wants to record an event as a first-class
MAGMA memory. It is intentionally thin and side-effect oriented:

    1. Generate a stable node id + embedding (Ollama nomic-embed-text, fallback ok)
    2. CREATE a :MemoryNode in Neo4j with content/category/timestamp/embedding_hash
    3. INSERT a row into Postgres `memory_provenance` so the unified memory bus
       can fast-query "who wrote this and at what visibility tier"
    4. Stitch the new node into the graph by:
         - explicit `RELATED_TO` edges to caller-provided neighbours
         - auto-inferred `RELATED_TO` edges to the 50 most-recent nodes for the
           same daemon when cosine similarity > 0.7
         - an `OWNED_BY` edge pointing at a per-daemon pseudo-node
           (`daemon:<name>`) so memories cluster by their producer

The runtime hook pattern: daemons fire-and-forget by calling
``write_event_memory(...)``. The function degrades gracefully — Neo4j down,
Ollama down, Postgres down: each path is wrapped so a memory write never
crashes a daemon. Worst case: a node gets written without an embedding, or
provenance is skipped, but the caller still receives a node id.

Architecture spec:
    ~/.claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory/
        shared/RUNTIME-MEMORY-ARCHITECTURE.md

Existing schema referenced:
    shared/magma.py        — Neo4j :MemoryNode + Cypher patterns, _get_embedding
    scripts/migrations/042-unified-memory-bus.sql — memory_provenance schema
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("shared.magma_writer")


# ── Tunables ─────────────────────────────────────────────────────────

#: Cosine similarity threshold above which two nodes get auto-linked
#: with a RELATED_TO edge during write_event_memory().
SIMILARITY_THRESHOLD: float = 0.7

#: How many recent nodes (per daemon) to scan for auto edge inference.
RECENT_NODE_SCAN_LIMIT: int = 50

#: Per-daemon pseudo-node id prefix used as the target of OWNED_BY edges.
DAEMON_NODE_PREFIX: str = "daemon:"


# ── Embedding ────────────────────────────────────────────────────────


async def _embed(text: str) -> list[float] | None:
    """Compute an embedding via Ollama. Degrades gracefully on any failure."""
    try:
        import httpx

        from shared.config import config

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{config.ollama.host}/api/embeddings",
                json={
                    "model": getattr(config.ollama, "embed_model", "nomic-embed-text"),
                    "prompt": text[:1000],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("embedding")
            if isinstance(embedding, list) and embedding:
                return [float(x) for x in embedding]
            return None
    except Exception as exc:  # noqa: BLE001 — runtime hook must never crash callers
        logger.warning("magma_writer: embedding failed (%s) — continuing without", exc)
        return None


def _embedding_hash(vec: list[float]) -> str:
    import hashlib

    raw = json.dumps(vec[:8], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── Neo4j helpers (mirrors shared/magma.py pattern) ──────────────────


def _get_driver() -> Any | None:
    """Borrow the existing MAGMA driver if available, else return None."""
    try:
        from shared.magma import _get_driver as _shared_get_driver

        return _shared_get_driver()
    except Exception as exc:  # noqa: BLE001
        logger.debug("magma_writer: shared.magma driver unavailable (%s)", exc)
        return None


async def _run_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
    """Run a Cypher query off the asyncio loop. Returns list of result dicts.

    The neo4j Python driver is synchronous, so we delegate to a worker thread to
    keep the daemon event loop responsive.
    """
    driver = _get_driver()
    if driver is None:
        return []

    def _do() -> list[dict[str, Any]]:
        with driver.session() as session:
            result = session.run(query, **params)
            return [dict(record) for record in result]

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:  # noqa: BLE001
        logger.warning("magma_writer: cypher failed (%s)", exc)
        return []


# ── Provenance (Postgres) ────────────────────────────────────────────


async def _record_provenance(
    node_id: str,
    daemon: str,
    event_type: str,
    visibility: str,
) -> None:
    """Best-effort write into ``memory_provenance``. Never raises."""
    try:
        from shared.db import execute

        await execute(
            """INSERT INTO memory_provenance
                   (magma_node_id, source_daemon, source_table,
                    source_record_id, visibility, ingested_at)
               VALUES (%s, %s, %s, %s, %s, NOW())
               ON CONFLICT (magma_node_id) DO NOTHING""",
            (node_id, daemon, event_type, node_id, visibility),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("magma_writer: provenance write skipped (%s)", exc)


# ── Public API ───────────────────────────────────────────────────────


async def ensure_daemon_node(daemon: str) -> str:
    """Idempotently create a per-daemon pseudo-node.

    The pseudo-node acts as the target of all ``OWNED_BY`` edges so that the
    brain graph naturally clusters each daemon's memories together. The id is
    deterministic: ``daemon:<name>``.
    """
    node_id = f"{DAEMON_NODE_PREFIX}{daemon}"
    await _run_cypher(
        """MERGE (d:MemoryNode {node_id: $node_id})
           ON CREATE SET d.content = $content,
                         d.category = 'daemon_root',
                         d.timestamp = $ts,
                         d.metadata = '{}',
                         d.embedding_hash = '',
                         d.domain = 'system',
                         d.consolidated = false,
                         d.is_pseudo = true""",
        node_id=node_id,
        content=f"Pseudo-node clustering all memories owned by daemon '{daemon}'.",
        ts=datetime.utcnow().isoformat(),
    )
    return node_id


async def get_recent_nodes_for_daemon(
    daemon: str,
    limit: int = RECENT_NODE_SCAN_LIMIT,
) -> list[dict[str, Any]]:
    """Return the most recent N MAGMA nodes owned by ``daemon``.

    Each dict contains ``node_id``, ``content``, ``timestamp``, ``embedding_hash``
    and ``embedding`` (the recomputed embedding for similarity scoring — Neo4j
    only stores the hash, so we re-embed the content snippet on demand).
    """
    rows = await _run_cypher(
        """MATCH (root:MemoryNode {node_id: $root_id})<-[:OWNED_BY]-(n:MemoryNode)
           WHERE coalesce(n.is_pseudo, false) = false
           RETURN n.node_id        AS node_id,
                  n.content        AS content,
                  n.timestamp      AS timestamp,
                  n.embedding_hash AS embedding_hash
           ORDER BY n.timestamp DESC
           LIMIT $limit""",
        root_id=f"{DAEMON_NODE_PREFIX}{daemon}",
        limit=int(limit),
    )

    enriched: list[dict[str, Any]] = []
    for row in rows:
        content = row.get("content") or ""
        embedding = await _embed(content) if content else None
        enriched.append(
            {
                "node_id": row.get("node_id"),
                "content": content,
                "timestamp": row.get("timestamp"),
                "embedding_hash": row.get("embedding_hash"),
                "embedding": embedding,
            }
        )
    return enriched


async def write_event_memory(
    daemon: str,
    event_type: str,
    summary: str,
    payload: dict | None = None,
    visibility: str = "tier1",
    confidence: float = 0.7,
    related_to: list[str] | None = None,
) -> str:
    """Write a runtime event as a MAGMA memory node + provenance + edges.

    This is the runtime hook every daemon calls to record a meaningful event.
    The write is best-effort: if Neo4j or Postgres or Ollama are unavailable,
    the function still returns a node id so the caller's flow keeps moving.

    Args:
        daemon: Producing daemon name (e.g. ``"titan"``, ``"hermes"``).
        event_type: Logical event class (e.g. ``"campaign_sent"``,
            ``"alert_triggered"``). Stored as ``source_table`` in provenance.
        summary: Human-readable one-line description of the event. This is the
            text that gets embedded and stored as the node's ``content``.
        payload: Optional structured payload preserved alongside the node.
        visibility: Tier label, default ``"tier1"`` (private to the producing
            daemon). Use ``"tier2"`` for cross-daemon and ``"public"`` for
            operator-visible memories.
        confidence: Subjective confidence score in [0, 1]. Stored on the node.
        related_to: Optional list of existing node ids to link via RELATED_TO.

    Returns:
        The new node id (UUID-based, prefixed ``magma_``).
    """
    node_id = f"magma_{uuid.uuid4().hex[:12]}"
    ts = datetime.utcnow().isoformat()
    payload = payload or {}

    # 1. Embedding (graceful degradation if Ollama is down)
    embedding = await _embed(summary)
    emb_hash = _embedding_hash(embedding) if embedding else ""

    # 2. Daemon pseudo-node (idempotent)
    daemon_node_id = await ensure_daemon_node(daemon)

    # 3. Insert the MemoryNode
    metadata = {
        "event_type": event_type,
        "payload": payload,
        "visibility": visibility,
        "confidence": float(confidence),
        "daemon": daemon,
    }
    await _run_cypher(
        """CREATE (n:MemoryNode {
               node_id: $node_id,
               content: $content,
               category: $category,
               timestamp: $ts,
               metadata: $meta_json,
               embedding_hash: $emb_hash,
               domain: $domain,
               consolidated: false,
               confidence: $confidence,
               visibility: $visibility,
               access_count: 0,
               is_pseudo: false
           })""",
        node_id=node_id,
        content=summary[:2000],
        category=event_type,
        ts=ts,
        meta_json=json.dumps(metadata, default=str),
        emb_hash=emb_hash,
        domain=daemon,
        confidence=float(confidence),
        visibility=visibility,
    )

    # 4. OWNED_BY edge → daemon pseudo-node
    await _run_cypher(
        """MATCH (n:MemoryNode {node_id: $node_id})
           MATCH (d:MemoryNode {node_id: $daemon_node})
           MERGE (n)-[:OWNED_BY]->(d)""",
        node_id=node_id,
        daemon_node=daemon_node_id,
    )

    # 5. Explicit RELATED_TO edges from caller
    for target_id in related_to or []:
        if not target_id or target_id == node_id:
            continue
        await _run_cypher(
            """MATCH (a:MemoryNode {node_id: $a})
               MATCH (b:MemoryNode {node_id: $b})
               MERGE (a)-[r:RELATED_TO]->(b)
                 ON CREATE SET r.source = 'explicit', r.score = 1.0""",
            a=node_id,
            b=target_id,
        )

    # 6. Auto-inferred RELATED_TO edges via embedding similarity
    if embedding:
        try:
            recents = await get_recent_nodes_for_daemon(daemon, RECENT_NODE_SCAN_LIMIT)
            for other in recents:
                other_id = other.get("node_id")
                other_emb = other.get("embedding")
                if not other_id or other_id == node_id or not other_emb:
                    continue
                score = _cosine(embedding, other_emb)
                if score > SIMILARITY_THRESHOLD:
                    await _run_cypher(
                        """MATCH (a:MemoryNode {node_id: $a})
                           MATCH (b:MemoryNode {node_id: $b})
                           MERGE (a)-[r:RELATED_TO]->(b)
                             ON CREATE SET r.source = 'auto', r.score = $score
                             ON MATCH  SET r.score = CASE
                                 WHEN r.score IS NULL OR r.score < $score
                                 THEN $score ELSE r.score END""",
                        a=node_id,
                        b=other_id,
                        score=float(score),
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("magma_writer: auto-edge inference skipped (%s)", exc)

    # 7. Provenance (Postgres)
    await _record_provenance(node_id, daemon, event_type, visibility)

    logger.info(
        "magma_writer: wrote event memory node=%s daemon=%s event=%s vis=%s",
        node_id,
        daemon,
        event_type,
        visibility,
    )
    return node_id


__all__ = [
    "DAEMON_NODE_PREFIX",
    "RECENT_NODE_SCAN_LIMIT",
    "SIMILARITY_THRESHOLD",
    "ensure_daemon_node",
    "get_recent_nodes_for_daemon",
    "write_event_memory",
]
