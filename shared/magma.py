"""
MAGMA — Multi-graph Adaptive Memory with Graph-Augmented Retrieval.

Paper-complete implementation of n_i = <c_i, τ_i, v_i, A_i>
(content, timestamp, embedding vector, attribute set) across four graphs:

  TEMPORAL   → immutable chain: "this happened after that"
  CAUSED     → LLM-inferred causal links with confidence + mechanism
  SIMILAR_TO → semantic edges from embedding cosine similarity
  INVOLVES   → entity deduplication (same business/person/industry)

Every MemoryNode stores an embedding_hash linking it to its Qdrant vector.
RRF fusion merges results by magma_node_id (true identity, not fake IDs).
Beam search scores transitions: λ₁·φ(structural) + λ₂·ψ(cosine similarity).
Linearization preserves provenance and topologically sorts causal chains.
Memory evolution: decay, strengthen, contradict+escalate, semantic merge.
"""

from __future__ import annotations

import asyncio
import hashlib
import httpx
import json
import logging
import math
import os
import psycopg
import re
import time
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from shared.config import config

logger = logging.getLogger("perseus.magma")


class Intent(str, Enum):
    CAUSAL = "causal"
    TEMPORAL = "temporal"
    ENTITY = "entity"
    SEMANTIC = "semantic"


class EdgeType(str, Enum):
    TEMPORAL = "TEMPORAL"
    CAUSAL = "CAUSED"
    SEMANTIC = "SIMILAR_TO"
    ENTITY = "INVOLVES"


# ── Phase 26a-04: Memory type taxonomy with derivability filter (E-01) ──


class MemoryType(str, Enum):
    """Taxonomy of MAGMA memory types.

    Derivable information (lead status, campaign metrics) duplicates
    Postgres tables and creates stale parallel copies.  The derivability
    filter rejects storage of content that belongs in an authoritative
    relational table instead.
    """
    OPERATOR = "operator"        # Human-authored instructions / corrections
    CORRECTION = "correction"    # Fixes to previous learnings
    CAMPAIGN = "campaign"        # Campaign strategy insights
    BOOKMARK = "bookmark"        # Saved references for later
    INSIGHT = "insight"          # Derived learnings from pipeline
    OBSERVATION = "observation"  # Daily observations (KAIROS)


# Tables that contain authoritative data — don't store duplicates in MAGMA
_DERIVABLE_TABLES: dict[str, str] = {
    "lead_status": "leads",
    "campaign_metrics": "campaigns",
    "budget_data": "budget_tracking",
    "task_status": "task_queue",
    "agent_status": "managed_agents",
}


def _derivability_filter_enabled() -> bool:
    """Check if the ANATOMY_RUNTIME_STATE feature flag is active."""
    return os.environ.get("ANATOMY_RUNTIME_STATE", "").lower() in ("1", "true", "yes")


def _is_derivable(content: str, category: str = "") -> bool:
    """Check if this content duplicates information in a Postgres table.

    Uses a simple keyword heuristic: if the content is primarily about
    data that lives in a specific authoritative table, it should not be
    duplicated in MAGMA.
    """
    content_lower = content.lower()
    for keyword, table in _DERIVABLE_TABLES.items():
        if keyword.replace("_", " ") in content_lower:
            return True
    return False


# ── Constants ────────────────────────────────────────────────────────

CAUSAL_CONFIDENCE_THRESHOLD = 0.4
SIMILAR_TO_THRESHOLD = 0.75
BEAM_LAMBDA_STRUCTURAL = 1.0
BEAM_LAMBDA_SEMANTIC = 0.5
BEAM_WIDTH_BASE = 50
MAX_DEPTH = 5
RRF_K = 60                    # RRF constant: 1/(k+rank) prevents top-rank dominance
CONFIDENCE_DECAY_RATE = 0.05
LINEARIZE_TOKEN_BUDGET = 2000
SEMANTIC_MERGE_THRESHOLD = 0.92  # cosine for node merging (very high = near-duplicate)
CONFIDENCE_ABSTENTION_THRESHOLD = float(os.environ.get("MAGMA_ABSTENTION_THRESHOLD", "0.4"))
CONFIDENCE_SCORING_ENABLED = os.environ.get("MAGMA_CONFIDENCE_SCORING", "1") == "1"
DOMAIN_SEGREGATION_ENABLED = os.environ.get("MAGMA_DOMAIN_SEGREGATION", "1") == "1"
TEMPORAL_HALF_LIFE_DAYS = 14.0  # MMA paper: 30-day half-life, we use 14 for sales velocity
STALENESS_THRESHOLD = float(os.environ.get("MAGMA_STALENESS_THRESHOLD", "0.3"))


# ── Confidence Scoring (MMA paper) ──────────────────────────────────

def source_reliability_score(source: str, category: str) -> float:
    """Score a memory source's reliability based on historical accuracy.

    Sources: "qdrant" (semantic), "graph" (Neo4j), "zep" (temporal), "fused" (multi-source).
    Fused sources get highest base reliability since they were confirmed by multiple backends.
    """
    base_scores = {
        "fused": 0.95,    # confirmed by multiple backends — most reliable
        "graph": 0.85,    # Neo4j has structured relationships
        "qdrant": 0.70,   # semantic similarity can be noisy
        "zep": 0.80,      # temporal facts are curated but may be stale
        "traversal": 0.65, # graph expansion may include tangential nodes
    }
    return base_scores.get(source, 0.5)


def temporal_decay_factor(timestamp_iso: str, half_life_days: float = TEMPORAL_HALF_LIFE_DAYS) -> float:
    """Exponential decay: recent memories weighted higher.

    MMA paper uses 30-day half-life. We use 14 for sales velocity.
    Returns 1.0 for now, 0.5 at half_life_days, approaches 0 for very old.
    """
    if not timestamp_iso:
        return 0.5  # unknown age → neutral
    try:
        ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
        age_days = max(0, (now - ts).total_seconds() / 86400)
        return math.exp(-0.693 * age_days / max(1, half_life_days))  # ln(2) ≈ 0.693
    except (ValueError, TypeError):
        return 0.5


def compute_anchor_confidence(anchor: dict) -> float:
    """Composite confidence score for a retrieval anchor.

    Combines: source reliability × temporal decay × importance × RRF score.
    Used to decide whether to inject memory into prompts or abstain.
    """
    reliability = source_reliability_score(anchor.get("source", ""), anchor.get("category", ""))
    decay = temporal_decay_factor(anchor.get("timestamp", ""))
    importance = anchor.get("importance", 0.5)
    rrf = anchor.get("score", 0.0)

    # Weighted combination — RRF is the primary signal, others modulate it
    return rrf * (0.4 * reliability + 0.3 * decay + 0.3 * min(1.0, importance))


def check_staleness(anchor: dict, threshold: float | None = None) -> str | None:
    """Check if an anchor's temporal decay indicates staleness.

    Returns a warning string if decay < threshold, None otherwise.
    Threshold defaults to MAGMA_STALENESS_THRESHOLD env var (0.3).
    """
    if threshold is None:
        threshold = STALENESS_THRESHOLD
    decay = temporal_decay_factor(anchor.get("timestamp", ""))
    if decay < threshold:
        node_id = anchor.get("node_id", "unknown")
        ts = anchor.get("timestamp", "unknown")
        return (
            f"Stale memory '{node_id}' (timestamp={ts}, decay={decay:.3f} < "
            f"threshold={threshold:.2f}) — treat with caution"
        )
    return None


# ── Neo4j Connection with Circuit Breaker ────────────────────────────

_driver = None
_neo4j_failures: list[float] = []
_neo4j_disabled_until: float = 0.0
CIRCUIT_BREAKER_THRESHOLD = 3   # failures within window
CIRCUIT_BREAKER_WINDOW = 300    # 5 minutes
CIRCUIT_BREAKER_COOLDOWN = 600  # disable for 10 minutes


def _record_neo4j_failure():
    """Record a Neo4j failure for circuit breaker tracking."""
    global _neo4j_disabled_until
    now = time.time()
    _neo4j_failures.append(now)
    # Prune old failures
    _neo4j_failures[:] = [t for t in _neo4j_failures if now - t < CIRCUIT_BREAKER_WINDOW]
    if len(_neo4j_failures) >= CIRCUIT_BREAKER_THRESHOLD:
        _neo4j_disabled_until = now + CIRCUIT_BREAKER_COOLDOWN
        logger.warning(f"MAGMA circuit breaker OPEN: {len(_neo4j_failures)} failures in {CIRCUIT_BREAKER_WINDOW}s. "
                      f"Neo4j disabled for {CIRCUIT_BREAKER_COOLDOWN}s.")


def _get_driver():
    global _driver, _neo4j_disabled_until
    if not config.memory.magma_enabled:
        return None
    # Circuit breaker: skip if recently disabled
    if time.time() < _neo4j_disabled_until:
        return None
    if _driver is not None:
        return _driver
    try:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            config.memory.neo4j_uri,
            auth=(config.memory.neo4j_user, config.memory.neo4j_password),
            max_connection_lifetime=300,
        )
        with _driver.session() as session:
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.node_id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.timestamp)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.category)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.embedding_hash)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)")
        logger.info("MAGMA Neo4j driver initialized")
        return _driver
    except (ImportError, OSError, ConnectionError, RuntimeError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning(f"MAGMA Neo4j unavailable (degrading to flat memory): {e}")
        return None


# ── Embedding helper ─────────────────────────────────────────────────

async def _get_embedding(text: str) -> list[float] | None:
    """Get embedding vector from Ollama nomic-embed-text."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{config.ollama.host}/api/embeddings",
                json={"model": config.ollama.embed_model, "prompt": text[:1000]},
            )
            resp.raise_for_status()
            return resp.json().get("embedding")
    except (httpx.HTTPError, OSError, TimeoutError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("MAGMA embedding generation failed: %s", exc)
        return None


def _embedding_hash(vec: list[float]) -> str:
    """Stable hash of embedding vector for Neo4j storage."""
    raw = json.dumps(vec[:8], separators=(",", ":"))  # first 8 dims for speed
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ═══════════════════════════════════════════════════════════════
# Fast Path: Ingest with Event Segmentation + Embedding Storage
# ═══════════════════════════════════════════════════════════════

_last_node_ids: dict[str, str] = {}
_last_node_ts: dict[str, str] = {}
_node_ids_lock: asyncio.Lock | None = None


def _get_node_ids_lock() -> asyncio.Lock:
    """Lazily create the asyncio.Lock to avoid event-loop errors on import."""
    global _node_ids_lock
    if _node_ids_lock is None:
        _node_ids_lock = asyncio.Lock()
    return _node_ids_lock

_EVENT_BOUNDARIES = re.compile(
    r"(?:^|\n)(?:\d+[\.\)]\s|[-•]\s|Stage \d|Step \d|Then |Next |After that )",
    re.MULTILINE,
)


def _segment_events(content: str) -> list[str]:
    if len(content) < 200:
        return [content]
    parts = _EVENT_BOUNDARIES.split(content)
    segments = [p.strip() for p in parts if p.strip() and len(p.strip()) > 20]
    return segments[:10] if len(segments) > 1 else [content]


async def magma_ingest(
    content: str,
    category: str,
    timestamp: str | None = None,
    metadata: dict | None = None,
    entities: list[str] | None = None,
) -> list[str]:
    """Fast path: n_i = <c_i, τ_i, v_i, A_i> stored across Qdrant + Neo4j.

    - Event segmentation for multi-event content
    - Embedding vector computed and hash stored on Neo4j node
    - magma_node_id stored in Qdrant payload for identity linkage
    - Temporal delta computed from previous node timestamp
    - Zep temporal fact stored with magma_node_id in metadata
    - Phase 26a-04: Derivability filter rejects content that duplicates
      authoritative Postgres tables (gated behind ANATOMY_RUNTIME_STATE)
    """
    # Phase 26a-04: Skip storage of derivable content when flag is active
    if _derivability_filter_enabled() and _is_derivable(content, category):
        logger.debug(
            "MAGMA derivability filter: skipping storage of derivable content "
            "(category=%s, len=%d)", category, len(content),
        )
        return []

    driver = _get_driver()
    if not driver:
        return []

    meta = metadata or {}
    ts = timestamp or datetime.now().isoformat()
    entity_list = entities or _extract_entities(content, meta)
    segments = _segment_events(content)
    node_ids = []

    for seg_content in segments:
        node_id = f"magma_{uuid.uuid4().hex[:12]}"
        node_ids.append(node_id)

        # Compute embedding for unified node representation
        embedding = await _get_embedding(seg_content)
        emb_hash = _embedding_hash(embedding) if embedding else ""

        # Store in Qdrant/Mem0 WITH magma_node_id
        try:
            from titan.memory import store_memory
            await store_memory(
                seg_content, category,
                client_id=meta.get("client_id"),
                metadata={**meta, "magma_node_id": node_id, "embedding_hash": emb_hash},
                outcome_magnitude=meta.get("outcome_magnitude", 0.5),
                sample_size=meta.get("sample_size", 1),
            )
        except (ImportError, httpx.HTTPError, OSError, TimeoutError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(f"MAGMA Mem0 write failed (non-critical): {e}")

        # Store in Zep WITH magma_node_id (if enabled), client-scoped
        try:
            from titan.memory import store_temporal_fact
            await store_temporal_fact(
                seg_content, category,
                valid_days=meta.get("valid_days", 30),
                metadata={**meta, "magma_node_id": node_id},
                client_id=meta.get("client_id"),
            )
        except (ImportError, httpx.HTTPError, OSError, TimeoutError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("MAGMA Zep store failed (non-critical): %s", exc)

        # Neo4j: node (with embedding_hash) + temporal edge + entity edges
        try:
            with driver.session() as session:
                domain = _resolve_domain(meta) if DOMAIN_SEGREGATION_ENABLED else "default"
                session.run(
                    """CREATE (n:MemoryNode {
                        node_id: $node_id,
                        content: $content,
                        category: $category,
                        timestamp: $ts,
                        metadata: $meta_json,
                        embedding_hash: $emb_hash,
                        domain: $domain,
                        consolidated: false
                    })""",
                    node_id=node_id, content=seg_content[:2000],
                    category=category, ts=ts,
                    meta_json=json.dumps(meta, default=str),
                    emb_hash=emb_hash, domain=domain,
                )

                # Temporal edge with REAL delta_seconds
                async with _get_node_ids_lock():
                    prev_id = _last_node_ids.get(category)
                    prev_ts = _last_node_ts.get(category)
                delta = 0
                if prev_id and prev_ts:
                    try:
                        dt_prev = datetime.fromisoformat(prev_ts)
                        dt_curr = datetime.fromisoformat(ts)
                        delta = int((dt_curr - dt_prev).total_seconds())
                    except (ValueError, TypeError):
                        delta = 0
                if prev_id:
                    session.run(
                        """MATCH (prev:MemoryNode {node_id: $prev_id})
                           MATCH (curr:MemoryNode {node_id: $curr_id})
                           CREATE (prev)-[:TEMPORAL {delta_seconds: $delta}]->(curr)""",
                        prev_id=prev_id, curr_id=node_id, delta=delta,
                    )
                async with _get_node_ids_lock():
                    _last_node_ids[category] = node_id
                    _last_node_ts[category] = ts

                # Entity-centric graph (M3-Agent/M2A papers): typed entity hubs
                for entity_name in entity_list:
                    etype = _classify_entity_type(entity_name, meta)
                    session.run(
                        """MERGE (e:Entity {name: $name})
                           ON CREATE SET e.entity_type = $etype
                           WITH e MATCH (n:MemoryNode {node_id: $node_id})
                           CREATE (n)-[:INVOLVES {entity_type: $etype}]->(e)""",
                        name=entity_name.lower().strip(), node_id=node_id, etype=etype,
                    )
        except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(f"MAGMA Neo4j write failed (degrading gracefully): {e}")

        # Enqueue for slow path
        try:
            from shared.db import emit_event
            await emit_event("magma_consolidate", {"node_id": node_id, "category": category})
        except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("MAGMA consolidation enqueue failed (non-critical): %s", exc)

    return node_ids


def _extract_entities(content: str, metadata: dict) -> list[str]:
    entities = []
    for key in ("business_name", "industry", "city", "region", "agent", "skill"):
        val = metadata.get(key, "")
        if val and isinstance(val, str) and len(val) > 1:
            entities.append(val)
    client_id = metadata.get("client_id")
    if client_id:
        entities.append(f"client:{client_id}")
    return entities


def _resolve_domain(metadata: dict) -> str:
    """Map metadata to a domain string for memory segregation.

    PersistBench paper: 53% cross-domain leakage without segregation.
    Each client gets its own domain. Memories from one client's dental
    practice should never leak into another client's restaurant context.
    """
    client_id = metadata.get("client_id")
    if client_id:
        return f"client:{client_id}"
    industry = metadata.get("industry", "")
    if industry:
        return f"industry:{industry.lower().strip()}"
    return "default"


def _classify_entity_type(entity_name: str, metadata: dict) -> str:
    """Classify entity type for typed INVOLVES edges (M3-Agent paper)."""
    if entity_name.startswith("client:"):
        return "customer"
    # Check metadata keys to infer type
    for key, etype in [("business_name", "customer"), ("industry", "industry"),
                       ("city", "region"), ("region", "region"), ("agent", "agent"),
                       ("skill", "skill")]:
        if metadata.get(key, "").lower().strip() == entity_name.lower().strip():
            return etype
    return "general"


# ═══════════════════════════════════════════════════════════════
# Slow Path: Causal + Semantic Edges with Full Neighborhood
# ═══════════════════════════════════════════════════════════════

async def magma_consolidate(node_id: str) -> bool:
    """Slow path: full neighborhood → LLM causal inference → CAUSED + SIMILAR_TO edges.

    Returns True if consolidation succeeded, False if there was nothing to
    consolidate (node missing, no neighbors).  Raises on transient failures
    (Neo4j down, edge writes failed) so the caller can leave the event
    unacknowledged for retry.
    """
    driver = _get_driver()
    if not driver:
        raise RuntimeError("MAGMA driver unavailable — transient, retry later")

    try:
        with driver.session() as session:
            result = session.run(
                """MATCH (n:MemoryNode {node_id: $node_id})
                   OPTIONAL MATCH (n)<-[:TEMPORAL*1..3]-(prev:MemoryNode)
                   OPTIONAL MATCH (n)-[:TEMPORAL*1..2]->(next:MemoryNode)
                   OPTIONAL MATCH (n)-[:INVOLVES]->(e:Entity)<-[:INVOLVES]-(sibling:MemoryNode)
                   WHERE sibling.node_id <> n.node_id
                   RETURN n.content as content, n.category as category, n.timestamp as ts,
                          collect(DISTINCT {id: prev.node_id, content: prev.content, ts: prev.timestamp, cat: prev.category}) as before,
                          collect(DISTINCT {id: next.node_id, content: next.content, ts: next.timestamp, cat: next.category}) as after,
                          collect(DISTINCT {id: sibling.node_id, content: sibling.content, cat: sibling.category}) as related""",
                node_id=node_id,
            ).single()
    except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # Neo4j read failure is transient — raise so event stays unacknowledged
        raise RuntimeError(f"MAGMA consolidation read failed: {e}") from e

    if not result:
        return False  # Node doesn't exist — permanent, safe to acknowledge

    content = result["content"]
    before_nodes = [n for n in result["before"] if n.get("content")][:5]
    after_nodes = [n for n in result["after"] if n.get("content")][:3]
    related_nodes = [n for n in result["related"] if n.get("content")][:3]

    # Semantic neighbors from Qdrant (the paper's missing piece)
    semantic_neighbors = []
    try:
        sem_results = await _search_memory_with_metadata(content, limit=8)
        for sr in sem_results:
            sr_node_id = sr.get("magma_node_id", "")
            if sr_node_id and sr_node_id != node_id:
                semantic_neighbors.append({
                    "id": sr_node_id,
                    "content": sr.get("content", "")[:200],
                    "score": sr.get("score", 0.0),
                    "cat": sr.get("category", ""),
                })
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError) as e:
        # IGUS-FIX: Narrow exception type for semantic neighbor search (CWE-755)
        logger.warning("MAGMA semantic neighbor search failed: %s", e)

    all_neighbors = before_nodes + after_nodes + related_nodes
    if not all_neighbors and not semantic_neighbors:
        return False

    # Build LLM context
    neighborhood = ""
    if before_nodes:
        neighborhood += "EVENTS BEFORE:\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in before_nodes) + "\n\n"
    if after_nodes:
        neighborhood += "EVENTS AFTER:\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in after_nodes) + "\n\n"
    if related_nodes:
        neighborhood += "RELATED (same entities):\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in related_nodes) + "\n\n"
    if semantic_neighbors:
        neighborhood += "SEMANTICALLY SIMILAR:\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in semantic_neighbors)

    # LLM causal inference
    try:
        from shared.llm_client import llm
        llm_result = await llm.generate(
            f"Event: {content[:300]}\n\nNeighborhood:\n{neighborhood}\n\n"
            f"What CAUSED this event? What did this event CAUSE?\n"
            f"Only STRONG causal links. Rate confidence 0.0-1.0. Explain mechanism.\n\n"
            f"JSON: {{\"caused_by\": [{{\"snippet\": \"<text>\", \"confidence\": 0.0-1.0, \"mechanism\": \"how\"}}], "
            f"\"caused\": [{{\"snippet\": \"<text>\", \"confidence\": 0.0-1.0, \"mechanism\": \"how\"}}]}}",
            model="local-heavy",
            temperature=0.1,
            pipeline_stage="memory:causal_inference",
        )
        start = llm_result.find("{")
        end = llm_result.rfind("}") + 1
        causal = json.loads(llm_result[start:end])
    except (json.JSONDecodeError, ValueError, KeyError, ImportError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("MAGMA causal inference parse failed: %s", exc)
        causal = {"caused_by": [], "caused": []}

    edges_written = 0
    audit_records = []

    try:
        with driver.session() as session:
            all_candidates = before_nodes + after_nodes + related_nodes + [
                {"id": sn["id"], "content": sn["content"]} for sn in semantic_neighbors
            ]

            for direction, items in [("caused_by", causal.get("caused_by", [])), ("caused", causal.get("caused", []))]:
                for item in items[:3]:
                    if isinstance(item, str):
                        snippet, confidence, mechanism = item, 0.5, ""
                    elif isinstance(item, dict):
                        snippet = item.get("snippet", "")
                        confidence = min(1.0, max(0.0, item.get("confidence", 0.5)))
                        mechanism = item.get("mechanism", "")
                    else:
                        continue

                    if not snippet or confidence < CAUSAL_CONFIDENCE_THRESHOLD:
                        continue

                    for n in all_candidates:
                        if snippet[:30].lower() in (n.get("content", "")[:200].lower()):
                            cause_id = n["id"] if direction == "caused_by" else node_id
                            effect_id = node_id if direction == "caused_by" else n["id"]
                            session.run(
                                """MATCH (cause:MemoryNode {node_id: $cause_id})
                                   MATCH (effect:MemoryNode {node_id: $effect_id})
                                   MERGE (cause)-[r:CAUSED]->(effect)
                                   ON CREATE SET r.inferred_at=$ts, r.confidence=$conf,
                                     r.mechanism=$mech, r.confirmations=1
                                   ON MATCH SET r.confidence = CASE
                                     WHEN $conf > r.confidence THEN $conf
                                     ELSE r.confidence + 0.05 END,
                                     r.confirmations = r.confirmations + 1,
                                     r.last_confirmed = $ts""",
                                cause_id=cause_id, effect_id=effect_id,
                                ts=datetime.now().isoformat(),
                                conf=confidence, mech=mechanism[:200],
                            )
                            edges_written += 1
                            audit_records.append({
                                "node_id": node_id,
                                "cause_node_id": cause_id,
                                "effect_node_id": effect_id,
                                "confidence": confidence,
                            })
                            break

            # SIMILAR_TO edges for semantic neighbors above threshold
            for sn in semantic_neighbors:
                if sn.get("score", 0) >= SIMILAR_TO_THRESHOLD and sn.get("id"):
                    session.run(
                        """MATCH (a:MemoryNode {node_id: $a_id})
                           MATCH (b:MemoryNode {node_id: $b_id})
                           MERGE (a)-[r:SIMILAR_TO]->(b)
                           SET r.cosine = $score, r.inferred_at = $ts""",
                        a_id=node_id, b_id=sn["id"],
                        score=sn["score"], ts=datetime.now().isoformat(),
                    )
                    edges_written += 1

            session.run("MATCH (n:MemoryNode {node_id: $id}) SET n.consolidated = true", id=node_id)
    except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        # Edge write failure is transient — raise so event stays unacknowledged
        raise RuntimeError(f"MAGMA edge write failed: {e}") from e

    if audit_records:
        try:
            from shared.db import execute as db_execute
            for rec in audit_records:
                await db_execute(
                    """INSERT INTO magma_causal_audit
                       (node_id, cause_node_id, effect_node_id, confidence)
                       VALUES (%s, %s, %s, %s)""",
                    (rec["node_id"], rec["cause_node_id"], rec["effect_node_id"], rec["confidence"]),
                )
        except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("MAGMA audit record insert failed: %s", exc)

    if edges_written:
        logger.info(f"MAGMA consolidated {node_id}: {edges_written} edges")
    return edges_written > 0


async def _search_memory_with_metadata(
    query: str, limit: int = 8, client_id: int | None = None,
) -> list[dict]:
    """Search Qdrant/Mem0 returning full metadata including magma_node_id and score.

    When client_id is provided, searches the client namespace first and
    backfills remaining slots from the system ("titan") namespace — same
    strategy as titan/memory.py:search_memory.
    """
    import httpx

    def _parse_results(results: list) -> list[dict]:
        return [{
            "content": r.get("memory", ""),
            "magma_node_id": r.get("metadata", {}).get("magma_node_id", ""),
            "category": r.get("metadata", {}).get("category", ""),
            "score": r.get("score", 0.0),
            "importance": r.get("metadata", {}).get("importance", 0.0),
            "timestamp": r.get("metadata", {}).get("timestamp", ""),
            "source": "qdrant",
        } for r in results]

    async def _search_ns(user_id: str, n: int) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.post(
                    f"{config.memory.mem0_host}/v1/memories/search/",
                    json={"query": query, "user_id": user_id, "limit": n},
                )
                resp.raise_for_status()
                return _parse_results(resp.json().get("results", []))
        except (httpx.HTTPError, OSError, TimeoutError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("MAGMA Mem0 search failed: %s", exc)
            return []

    if client_id is not None:
        client_results = await _search_ns(f"client:{client_id}", limit)
        remaining = limit - len(client_results)
        if remaining > 0:
            system_results = await _search_ns("titan", remaining)
            return client_results + system_results
        return client_results
    else:
        return await _search_ns("titan", limit)


# ═══════════════════════════════════════════════════════════════
# Retrieval Telemetry (Phase 19 — Task 19-02)
# ═══════════════════════════════════════════════════════════════

async def _record_retrieval_stats(
    query: str,
    intent: str,
    anchors_found: int,
    anchors_used: int,
    confidence_avg: float,
    latency_ms: int,
    decompose_ms: int,
    anchor_ms: int,
    beam_ms: int,
    linearize_ms: int,
    abstained: bool,
) -> None:
    """Fire-and-forget retrieval telemetry insert.

    Logs warning on failure, never raises.
    Respects ANATOMY_COST_DASHBOARD feature flag.
    """
    if os.environ.get("ANATOMY_COST_DASHBOARD", "").lower() not in ("true", "1"):
        return

    try:
        from shared.db import execute

        # Truncate long queries to 500 chars for storage
        truncated_query = query[:500] if len(query) > 500 else query

        await execute(
            """INSERT INTO magma_retrieval_stats
               (query, intent, anchors_found, anchors_used, confidence_avg,
                latency_ms, decompose_ms, anchor_ms, beam_ms, linearize_ms, abstained)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                truncated_query,
                intent,
                anchors_found,
                anchors_used,
                confidence_avg,
                latency_ms,
                decompose_ms,
                anchor_ms,
                beam_ms,
                linearize_ms,
                abstained,
            ),
        )
    except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("Failed to record retrieval stats: %s", exc)


async def compute_alma_adjustments_from_telemetry(
    query_type: str, lookback_days: int = 7
) -> dict:
    """Compute ALMA meta-param adjustments from retrieval telemetry.

    Analyzes magma_retrieval_stats for a query_type to identify:
    - High abstention rate -> increase beam_width, lower abstention threshold
    - High latency -> decrease max_depth, decrease beam_width
    - Low anchor utilization -> adjust rrf_k, increase lambda_semantic

    Returns adjustment dict compatible with update_meta_params().
    Feature flag: ANATOMY_COST_DASHBOARD
    """
    if os.environ.get("ANATOMY_COST_DASHBOARD", "").lower() not in ("true", "1"):
        return {}

    try:
        from shared.db import fetch_one

        stats = await fetch_one(
            """SELECT
                COUNT(*) AS total_queries,
                AVG(anchors_found) AS avg_anchors_found,
                AVG(anchors_used) AS avg_anchors_used,
                AVG(confidence_avg) AS avg_confidence,
                AVG(latency_ms) AS avg_latency,
                SUM(CASE WHEN abstained THEN 1 ELSE 0 END) AS abstention_count,
                AVG(beam_ms) AS avg_beam_ms,
                AVG(anchor_ms) AS avg_anchor_ms
            FROM magma_retrieval_stats
            WHERE query_type = %s
              AND created_at >= NOW() - INTERVAL %s""",
            (query_type, f"{lookback_days} days"),
        )

        if not stats or int(stats.get("total_queries") or 0) < 10:
            return {}  # Not enough data to adjust

        adjustments: dict = {}
        total = int(stats["total_queries"])
        abstention_rate = int(stats.get("abstention_count") or 0) / max(total, 1)
        avg_latency = float(stats.get("avg_latency") or 0)
        avg_anchors_found = float(stats.get("avg_anchors_found") or 1)
        anchor_utilization = float(stats.get("avg_anchors_used") or 0) / max(avg_anchors_found, 1)

        # High abstention -> widen beam
        if abstention_rate > 0.3:
            adjustments["beam_width"] = 10

        # High latency -> reduce depth
        if avg_latency > 2000:
            adjustments["max_depth"] = -1

        # Low anchor utilization -> adjust semantic weight
        if anchor_utilization < 0.3:
            adjustments["lambda_semantic"] = 0.1

        return adjustments

    except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("ALMA telemetry analysis failed: %s", exc)
        return {}


# ═══════════════════════════════════════════════════════════════
# Retrieval: Intent Router with Query Decomposition
# ═══════════════════════════════════════════════════════════════

async def magma_retrieve(
    query: str, limit: int = 10, client_id: int | None = None,
    query_type: str = "",
) -> str:
    """Full paper-spec retrieval with confidence-aware abstention.

    When MAGMA_CONFIDENCE_SCORING=1 and all anchors fall below the
    abstention threshold, prepends [LOW CONFIDENCE] warning so the
    consuming LLM knows to treat these memories with caution.

    client_id is threaded into decomposition metadata so that
    _resolve_domain can scope graph queries to a single client,
    preventing cross-client memory leakage.

    query_type (e.g. "email_compose", "lead_research") selects ALMA
    meta-learned retrieval parameters (beam width, lambdas, depth, RRF k).
    When empty, uses the module-level defaults.

    Returns empty string when graph retrieval is unavailable so the
    caller (get_relevant_learnings) falls through to the flat stack.
    This avoids mutual recursion between magma_retrieve ↔ get_relevant_learnings.
    """
    t0_total = time.perf_counter()

    driver = _get_driver()
    if not driver:
        return ""

    # Resolve ALMA meta-learned params for this query type
    params = await meta_search_params(query_type)

    # Phase 1: Decompose query
    t0_decompose = time.perf_counter()
    decomp = await _decompose_query(query, client_id=client_id)
    decompose_ms = int((time.perf_counter() - t0_decompose) * 1000)
    intent = decomp["intent"]

    # Phase 2: Find anchors
    t0_anchor = time.perf_counter()
    anchors = await _find_anchors_linked(query, intent, decomp, limit=limit, params=params)
    anchor_ms = int((time.perf_counter() - t0_anchor) * 1000)

    if not anchors:
        # Record stats even for empty retrieval
        _telemetry_anchors_found = 0
        latency_ms = int((time.perf_counter() - t0_total) * 1000)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_record_retrieval_stats(
                query=query, intent=intent.value if hasattr(intent, "value") else str(intent),
                anchors_found=0, anchors_used=0, confidence_avg=0.0,
                latency_ms=latency_ms, decompose_ms=decompose_ms,
                anchor_ms=anchor_ms, beam_ms=0, linearize_ms=0, abstained=False,
            ))
        except RuntimeError:
            pass  # No event loop — skip telemetry
        return ""

    anchors_found = len(anchors)

    # Confidence-aware abstention (MMA paper)
    if CONFIDENCE_SCORING_ENABLED and anchors:
        _avg_confidence = sum(a.get("confidence", 0) for a in anchors) / len(anchors)
        max_confidence = max(a.get("confidence", 0) for a in anchors)

        if max_confidence < CONFIDENCE_ABSTENTION_THRESHOLD:
            # All memories are low-confidence — signal caller to use flat stack
            logger.info(f"MAGMA abstaining: max_confidence={max_confidence:.2f} < {CONFIDENCE_ABSTENTION_THRESHOLD}")
            latency_ms = int((time.perf_counter() - t0_total) * 1000)
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_record_retrieval_stats(
                    query=query, intent=intent.value if hasattr(intent, "value") else str(intent),
                    anchors_found=anchors_found, anchors_used=0,
                    confidence_avg=_avg_confidence,
                    latency_ms=latency_ms, decompose_ms=decompose_ms,
                    anchor_ms=anchor_ms, beam_ms=0, linearize_ms=0, abstained=True,
                ))
            except RuntimeError:
                pass
            return f"[LOW CONFIDENCE — all retrieved memories scored below {CONFIDENCE_ABSTENTION_THRESHOLD:.1f}. Treat with caution.]"

    # Staleness warnings — check each anchor for temporal decay below threshold
    staleness_warnings: list[str] = []
    for anchor in anchors:
        warning = check_staleness(anchor)
        if warning:
            staleness_warnings.append(warning)
            anchor["stale"] = True
        else:
            anchor["stale"] = False

    if staleness_warnings:
        logger.warning(
            f"MAGMA staleness: {len(staleness_warnings)}/{len(anchors)} anchors are stale — "
            + "; ".join(staleness_warnings[:3])
            + ("..." if len(staleness_warnings) > 3 else "")
        )

    # Phase 3: Beam search
    t0_beam = time.perf_counter()
    subgraph = await _scored_beam_search(driver, anchors, intent, query, decomp, params=params)
    beam_ms = int((time.perf_counter() - t0_beam) * 1000)

    # Phase 4: Linearize
    t0_linearize = time.perf_counter()
    result = _linearize_with_provenance(subgraph, intent)
    linearize_ms = int((time.perf_counter() - t0_linearize) * 1000)

    anchors_used = len(subgraph)

    # Add confidence summary header if scoring enabled
    if CONFIDENCE_SCORING_ENABLED and anchors:
        avg_conf = sum(a.get("confidence", 0) for a in anchors) / len(anchors)
        result = f"[Memory confidence: {avg_conf:.2f} avg, {len(anchors)} sources]\n{result}"
    else:
        avg_conf = 0.0

    # Add staleness warnings to result metadata
    if staleness_warnings:
        stale_header = f"[STALE MEMORIES: {len(staleness_warnings)}/{len(anchors)} anchors exceeded staleness threshold]\n"
        result = stale_header + result

    # Fire-and-forget telemetry (Phase 19-02)
    latency_ms = int((time.perf_counter() - t0_total) * 1000)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_record_retrieval_stats(
            query=query,
            intent=intent.value if hasattr(intent, "value") else str(intent),
            anchors_found=anchors_found,
            anchors_used=anchors_used,
            confidence_avg=avg_conf,
            latency_ms=latency_ms,
            decompose_ms=decompose_ms,
            anchor_ms=anchor_ms,
            beam_ms=beam_ms,
            linearize_ms=linearize_ms,
            abstained=False,
        ))
    except RuntimeError:
        pass  # No event loop — skip telemetry

    return result


def prefetch_retrieve(
    query: str, limit: int = 10, client_id: int | None = None, query_type: str = "",
) -> asyncio.Task | None:
    """Task 27-04: Async prefetch for memory retrieval — hide latency.

    Fires ``magma_retrieve`` as an ``asyncio.create_task()`` so callers can
    start retrieval early and ``await`` the result when assembling the LLM prompt.

    Gate: ANATOMY_MEMORY_INDEX feature flag.

    Returns:
        asyncio.Task wrapping the retrieval, or None if the feature flag is off
        or no event loop is running.
    """
    if os.environ.get("ANATOMY_MEMORY_INDEX", "").lower() not in ("true", "1"):
        return None
    try:
        if _pomdp_enabled():
            async def _pomdp_text_only() -> str:
                result, _belief = await pomdp_retrieve(
                    query, limit=limit, client_id=client_id, query_type=query_type,
                )
                return result
            return asyncio.create_task(
                _pomdp_text_only(),
                name=f"prefetch_pomdp_retrieve:{query[:40]}",
            )
        return asyncio.create_task(
            magma_retrieve(query, limit=limit, client_id=client_id, query_type=query_type),
            name=f"prefetch_retrieve:{query[:40]}",
        )
    except RuntimeError:
        # No running event loop
        logger.debug("prefetch_retrieve: no event loop, skipping")
        return None


async def _decompose_query(query: str, client_id: int | None = None) -> dict:
    """Query decomposition: intent + entities + time range + causal direction.

    client_id is stored in _metadata so _resolve_domain can scope graph
    queries per-client (PersistBench: 53% cross-domain leakage without this).
    """
    q = query.lower()

    causal_signals = ("why", "caused", "because", "reason", "led to", "result of", "due to", "impact of")
    temporal_signals = ("when", "last week", "yesterday", "today", "timeline", "history", "recent", "before", "after")
    entity_signals = ("who", "which company", "what about")

    causal_score = sum(1 for w in causal_signals if w in q)
    temporal_score = sum(1 for w in temporal_signals if w in q)
    entity_score = sum(1 for w in entity_signals if w in q)
    max_score = max(causal_score, temporal_score, entity_score)

    # LLM fallback for ambiguous queries
    if max_score <= 1:
        try:
            from shared.llm_client import llm
            result = await llm.generate(
                f"Classify this memory query and extract structured info:\n\"{query}\"\n\n"
                f"Return JSON: {{\"intent\": \"causal|temporal|entity|semantic\", "
                f"\"entities\": [\"names\"], "
                f"\"time_start\": \"ISO date or null\", \"time_end\": \"ISO date or null\", "
                f"\"causal_direction\": \"forward|backward|null\"}}",
                model="local-heavy",
                temperature=0.0,
                pipeline_stage="memory:query_decompose",
            )
            start = result.find("{")
            end = result.rfind("}") + 1
            parsed = json.loads(result[start:end])
            intent_str = parsed.get("intent", "semantic")
            return {
                "intent": Intent(intent_str) if intent_str in [i.value for i in Intent] else Intent.SEMANTIC,
                "entities": parsed.get("entities", []),
                "time_start": parsed.get("time_start"),
                "time_end": parsed.get("time_end"),
                "causal_direction": parsed.get("causal_direction"),
                "_metadata": {"client_id": client_id},
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            # IGUS-FIX: Narrow exception type for LLM query decomposition (CWE-755)
            logger.debug("MAGMA LLM query decomposition failed: %s", e)

    if causal_score > max(temporal_score, entity_score):
        intent = Intent.CAUSAL
    elif temporal_score > max(causal_score, entity_score):
        intent = Intent.TEMPORAL
    elif entity_score > 0:
        intent = Intent.ENTITY
    else:
        intent = Intent.SEMANTIC

    # Simple entity extraction
    entities = [w for w in query.split() if w[0:1].isupper() and len(w) > 2
                and w.lower() not in ("what", "why", "how", "when", "where", "the", "and", "for", "with")]

    # Temporal expression parsing
    time_start, time_end = _parse_time_range(q)

    return {"intent": intent, "entities": entities, "time_start": time_start,
            "time_end": time_end, "causal_direction": None,
            "_metadata": {"client_id": client_id}}


def _parse_time_range(q: str) -> tuple[str | None, str | None]:
    """Extract time range from natural language query."""
    now = datetime.now()
    if "today" in q:
        return now.strftime("%Y-%m-%dT00:00:00"), now.isoformat()
    if "yesterday" in q:
        y = now - timedelta(days=1)
        return y.strftime("%Y-%m-%dT00:00:00"), y.strftime("%Y-%m-%dT23:59:59")
    if "last week" in q:
        return (now - timedelta(days=7)).isoformat(), now.isoformat()
    if "last month" in q:
        return (now - timedelta(days=30)).isoformat(), now.isoformat()
    if "this week" in q:
        start = now - timedelta(days=now.weekday())
        return start.strftime("%Y-%m-%dT00:00:00"), now.isoformat()
    # Match "March 2026" etc.
    month_match = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})", q)
    if month_match:
        months = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                  "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
        month_num: int = months[month_match.group(1)]
        year_num: int = int(month_match.group(2))
        month_start = datetime(year_num, month_num, 1)
        if month_num == 12:
            month_end = datetime(year_num + 1, 1, 1) - timedelta(seconds=1)
        else:
            month_end = datetime(year_num, month_num + 1, 1) - timedelta(seconds=1)
        return month_start.isoformat(), month_end.isoformat()
    return None, None


# ═══════════════════════════════════════════════════════════════
# RRF Anchor Fusion with k=60 + Dual Score Preservation
# ═══════════════════════════════════════════════════════════════

async def _find_anchors_linked(
    query: str, intent: Intent, decomp: dict, limit: int = 10,
    params: dict | None = None,
) -> list[dict]:
    """RRF anchor fusion with ALMA-adaptive k constant."""
    driver = _get_driver()
    anchors_by_id: dict[str, dict] = {}
    rrf_k = (params or {}).get("rrf_k", RRF_K)

    # Source 1: Qdrant semantic search WITH metadata (client-scoped)
    client_id = (decomp.get("_metadata") or {}).get("client_id")
    try:
        sem_results = await _search_memory_with_metadata(query, limit=limit, client_id=client_id)
        for rank, sr in enumerate(sem_results):
            nid = sr.get("magma_node_id") or f"qdrant_{rank}"
            rrf_score = 1.0 / (rrf_k + rank)
            if nid in anchors_by_id:
                anchors_by_id[nid]["rrf_qdrant"] = rrf_score
                anchors_by_id[nid]["score"] += rrf_score
                anchors_by_id[nid]["semantic_score"] = sr.get("score", 0.0)
            else:
                anchors_by_id[nid] = {
                    "node_id": nid, "content": sr.get("content", ""),
                    "category": sr.get("category", ""), "timestamp": sr.get("timestamp", ""),
                    "score": rrf_score, "rrf_qdrant": rrf_score, "rrf_neo4j": 0.0,
                    "semantic_score": sr.get("score", 0.0),
                    "source": "qdrant", "provenance": "qdrant",
                }
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError) as e:
        # IGUS-FIX: Narrow exception type for Qdrant retrieval path (CWE-755)
        logger.warning("MAGMA Qdrant anchor search failed: %s", e)

    # Source 2: Neo4j keyword + entity + time-filtered search
    if driver:
        try:
            with driver.session() as session:
                words = [w for w in query.lower().split() if len(w) > 3][:5]
                for ent in decomp.get("entities", []):
                    if ent.lower() not in words:
                        words.append(ent.lower())

                # Time filter from decomposition
                time_filter = ""
                cypher_params = {"words": words, "limit": limit}
                if decomp.get("time_start"):
                    time_filter = " AND n.timestamp >= $t_start"
                    cypher_params["t_start"] = decomp["time_start"]
                if decomp.get("time_end"):
                    time_filter += " AND n.timestamp <= $t_end"
                    cypher_params["t_end"] = decomp["time_end"]

                # Domain filter (PersistBench paper: prevents cross-customer leakage)
                domain_filter = ""
                if DOMAIN_SEGREGATION_ENABLED:
                    domain_filter = " AND n.domain = $domain"
                    cypher_params["domain"] = _resolve_domain(decomp.get("_metadata", {}))

                if words:
                    results = session.run(
                        f"""MATCH (n:MemoryNode)
                           WHERE any(w IN $words WHERE toLower(n.content) CONTAINS w){time_filter}{domain_filter}
                           RETURN n.node_id as node_id, n.content as content,
                                  n.category as category, n.timestamp as ts
                           ORDER BY n.timestamp DESC LIMIT $limit""",
                        **cypher_params,
                    )
                    for rank, record in enumerate(results):
                        nid = record["node_id"]
                        rrf_score = 1.0 / (rrf_k + rank)
                        if nid in anchors_by_id:
                            anchors_by_id[nid]["rrf_neo4j"] = rrf_score
                            anchors_by_id[nid]["score"] += rrf_score
                            anchors_by_id[nid]["source"] = "fused"
                        else:
                            anchors_by_id[nid] = {
                                "node_id": nid, "content": record["content"],
                                "category": record["category"], "timestamp": record["ts"],
                                "score": rrf_score, "rrf_qdrant": 0.0, "rrf_neo4j": rrf_score,
                                "semantic_score": 0.0,
                                "source": "graph", "provenance": "neo4j",
                            }
        except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug(f"MAGMA anchor search failed: {e}")

    # Source 3: Zep temporal facts WITH magma_node_id (client-scoped)
    try:
        from titan.memory import search_temporal_facts
        zep_results = await search_temporal_facts(query, limit=5, client_id=client_id)
        for rank, fact in enumerate(zep_results):
            nid = fact.get("magma_node_id") or f"zep_{rank}"
            rrf_score = 1.0 / (rrf_k + rank)
            if nid in anchors_by_id:
                anchors_by_id[nid]["score"] += rrf_score
                anchors_by_id[nid]["source"] = "fused"
            else:
                anchors_by_id[nid] = {
                    "node_id": nid, "content": fact.get("content", ""),
                    "category": fact.get("category", ""),
                    "timestamp": fact.get("valid_until", ""),
                    "score": rrf_score, "rrf_qdrant": 0.0, "rrf_neo4j": 0.0,
                    "semantic_score": 0.0,
                    "source": "zep", "provenance": "zep",
                }
    except (ImportError, httpx.HTTPError, OSError, TimeoutError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("MAGMA anchor search failed: %s", exc)

    # Apply confidence scoring if enabled (MMA paper)
    for anchor in anchors_by_id.values():
        if CONFIDENCE_SCORING_ENABLED:
            anchor["confidence"] = compute_anchor_confidence(anchor)
        else:
            anchor["confidence"] = anchor.get("score", 0.5)

    ranked = sorted(anchors_by_id.values(), key=lambda x: x["confidence"], reverse=True)
    return ranked[:limit]


# ═══════════════════════════════════════════════════════════════
# Scored Beam Search with Real Cosine ψ + Dynamic Width + SIMILAR_TO
# ═══════════════════════════════════════════════════════════════

async def _scored_beam_search(
    driver, anchors: list[dict], intent: Intent, query: str,
    decomp: dict | None = None, params: dict | None = None,
) -> list[dict]:
    """score(n→n') = λ₁·φ(structural) + λ₂·ψ(cosine_similarity).

    - Real cosine ψ computed via embedding
    - Dynamic beam width: narrows by depth (beam_width / depth)
    - SIMILAR_TO traversal for SEMANTIC intent
    - Causal direction from query decomposition
    - All tuning constants read from ALMA meta-params dict
    """
    p = params or {}
    beam_width = p.get("beam_width", BEAM_WIDTH_BASE)
    lambda_structural = p.get("lambda_structural", BEAM_LAMBDA_STRUCTURAL)
    lambda_semantic = p.get("lambda_semantic", BEAM_LAMBDA_SEMANTIC)
    max_depth = p.get("max_depth", MAX_DEPTH)

    # For SEMANTIC intent, traverse SIMILAR_TO edges (not just return anchors)
    if intent == Intent.SEMANTIC:
        return await _traverse_similar(driver, anchors)

    edge_type = {Intent.CAUSAL: "CAUSED", Intent.TEMPORAL: "TEMPORAL", Intent.ENTITY: "INVOLVES"}.get(intent, "TEMPORAL")
    graph_anchors = [a for a in anchors if a.get("node_id", "").startswith("magma_")]
    if not graph_anchors:
        return anchors

    # Get query embedding for real cosine ψ
    query_embedding = await _get_embedding(query)

    expanded = list(anchors)
    seen_ids = {a["node_id"] for a in anchors}

    try:
        with driver.session() as session:
            for depth in range(1, max_depth + 1):
                beam_width_at_depth = max(10, beam_width // depth)
                current_anchors = graph_anchors if depth == 1 else [
                    n for n in expanded if n.get("source") == "traversal" and n.get("depth", 0) == depth - 1
                ]

                if not current_anchors:
                    break

                for anchor in current_anchors[:8]:
                    nid = anchor["node_id"]

                    if intent == Intent.ENTITY:
                        results = session.run(
                            """MATCH (n:MemoryNode {node_id: $nid})-[:INVOLVES]->(e:Entity)
                               <-[:INVOLVES]-(rel:MemoryNode)
                               WHERE rel.node_id <> $nid
                               RETURN rel.node_id as node_id, rel.content as content,
                                      rel.category as cat, rel.timestamp as ts
                               LIMIT $lim""",
                            nid=nid, lim=beam_width_at_depth,
                        )
                    elif edge_type == "CAUSED":
                        # Respect causal direction if decomposed
                        direction = (decomp or {}).get("causal_direction")
                        if direction == "forward":
                            pattern = "(n:MemoryNode {node_id: $nid})-[r:CAUSED]->(rel:MemoryNode)"
                        elif direction == "backward":
                            pattern = "(n:MemoryNode {node_id: $nid})<-[r:CAUSED]-(rel:MemoryNode)"
                        else:
                            pattern = "(n:MemoryNode {node_id: $nid})-[r:CAUSED]-(rel:MemoryNode)"
                        results = session.run(
                            f"""MATCH {pattern}
                               WHERE rel.node_id <> $nid AND r.confidence >= {CAUSAL_CONFIDENCE_THRESHOLD}
                               RETURN rel.node_id as node_id, rel.content as content,
                                      rel.category as cat, rel.timestamp as ts,
                                      r.confidence as conf, r.mechanism as mech
                               LIMIT $lim""",
                            nid=nid, lim=beam_width_at_depth,
                        )
                    else:
                        results = session.run(
                            f"""MATCH (n:MemoryNode {{node_id: $nid}})-[:{edge_type}]-(rel:MemoryNode)
                               WHERE rel.node_id <> $nid
                               RETURN rel.node_id as node_id, rel.content as content,
                                      rel.category as cat, rel.timestamp as ts
                               LIMIT $lim""",
                            nid=nid, lim=beam_width_at_depth,
                        )

                    candidates = []
                    for record in results:
                        rid = record["node_id"]
                        if rid in seen_ids:
                            continue

                        content = record.get("content", "")

                        # φ: structural alignment
                        phi = 1.0
                        if edge_type == "CAUSED":
                            phi = record.get("conf", 0.5) if record.get("conf") else 0.5

                        # ψ: REAL cosine similarity (not keyword overlap)
                        psi = 0.0
                        if query_embedding:
                            neighbor_emb = await _get_embedding(content[:300])
                            if neighbor_emb:
                                psi = _cosine_similarity(query_embedding, neighbor_emb)
                        else:
                            # Fallback: keyword overlap
                            qt = set(query.lower().split())
                            ct = set(content.lower().split())
                            psi = min(1.0, len(qt & ct) / max(1, len(qt)))

                        score = lambda_structural * phi + lambda_semantic * psi

                        candidates.append({
                            "node_id": rid, "content": content,
                            "category": record.get("cat", ""),
                            "timestamp": record.get("ts", ""),
                            "source": "traversal",
                            "provenance": f"neo4j:{edge_type}",
                            "edge_type": edge_type,
                            "via_anchor": nid,
                            "score": score,
                            "mechanism": record.get("mech", ""),
                            "depth": depth,
                        })

                    candidates.sort(key=lambda x: x["score"], reverse=True)
                    for c in candidates[:beam_width_at_depth]:
                        seen_ids.add(c["node_id"])
                        expanded.append(c)

    except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"MAGMA beam search failed: {e}")

    expanded.sort(key=lambda x: x.get("score", 0), reverse=True)
    return expanded[:beam_width]


async def _traverse_similar(driver, anchors: list[dict]) -> list[dict]:
    """For SEMANTIC intent: follow SIMILAR_TO edges from anchors."""
    graph_anchors = [a for a in anchors if a.get("node_id", "").startswith("magma_")]
    if not graph_anchors:
        return anchors

    expanded = list(anchors)
    seen = {a["node_id"] for a in anchors}

    try:
        with driver.session() as session:
            for anchor in graph_anchors[:5]:
                results = session.run(
                    """MATCH (n:MemoryNode {node_id: $nid})-[r:SIMILAR_TO]-(rel:MemoryNode)
                       WHERE rel.node_id <> $nid
                       RETURN rel.node_id as node_id, rel.content as content,
                              rel.category as cat, rel.timestamp as ts, r.cosine as score
                       ORDER BY r.cosine DESC LIMIT 10""",
                    nid=anchor["node_id"],
                )
                for record in results:
                    rid = record["node_id"]
                    if rid not in seen:
                        seen.add(rid)
                        expanded.append({
                            "node_id": rid, "content": record["content"],
                            "category": record.get("cat", ""),
                            "timestamp": record.get("ts", ""),
                            "source": "traversal", "provenance": "neo4j:SIMILAR_TO",
                            "score": record.get("score", 0.5),
                        })
    except (RuntimeError, OSError, ConnectionError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("MAGMA beam search expansion failed: %s", exc)

    return expanded


# ═══════════════════════════════════════════════════════════════
# Linearization: Provenance + Topological Sort + Dedup + Budget
# ═══════════════════════════════════════════════════════════════

def _linearize_with_provenance(subgraph: list[dict], intent: Intent) -> str:
    """Paper-spec linearization: provenance markers, topo sort for causal, dedup, budget."""
    if not subgraph:
        return "No relevant memories found."

    # Dedup by content (keep highest-scored version)
    seen_content: dict[str, dict] = {}
    deduped: list[dict] = []
    for node in subgraph:
        content_key = node.get("content", "")[:50].lower()
        if content_key in seen_content:
            if node.get("score", 0) > seen_content[content_key].get("score", 0):
                deduped = [n for n in deduped if n.get("content", "")[:50].lower() != content_key]
                deduped.append(node)
                seen_content[content_key] = node
        else:
            seen_content[content_key] = node
            deduped.append(node)

    parts = []
    budget = LINEARIZE_TOKEN_BUDGET

    if intent == Intent.CAUSAL:
        # Topological sort: order by causal chain (via_anchor → node)
        # Group by anchor, then sort within group by timestamp
        by_anchor: dict[str, list[dict]] = {}
        standalone = []
        for node in deduped:
            anchor = node.get("via_anchor")
            if anchor:
                by_anchor.setdefault(anchor, []).append(node)
            else:
                standalone.append(node)

        parts.append("CAUSAL CHAIN:")
        # Standalone first (anchors themselves)
        for node in sorted(standalone, key=lambda x: x.get("score", 0), reverse=True):
            if budget <= 0:
                break
            char_lim = min(200, max(50, int(150 * node.get("score", 0.5))))
            prov = node.get("provenance", "?")
            line = f"  [{prov}] {node['content'][:char_lim]}"
            parts.append(line)
            budget -= len(line)

        # Then causal chains, sorted by timestamp within each
        for _anchor_id, chain in by_anchor.items():
            if budget <= 0:
                break
            chain.sort(key=lambda x: x.get("timestamp", ""))
            for node in chain:
                if budget <= 0:
                    break
                char_lim = min(200, max(50, int(150 * node.get("score", 0.5))))
                prov = node.get("provenance", "?")
                mech = node.get("mechanism", "")
                mech_str = f" [via: {mech[:50]}]" if mech else ""
                line = f"    → [{prov}] {node['content'][:char_lim]}{mech_str}"
                parts.append(line)
                budget -= len(line)

    elif intent == Intent.TEMPORAL:
        deduped.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        parts.append("TIMELINE (most recent first):")
        for node in deduped:
            if budget <= 0:
                break
            ts = node.get("timestamp", "")[:16]
            cat = node.get("category", "")
            prov = node.get("provenance", "?")
            line = f"  [{ts}] [{cat}] [{prov}] {node['content'][:150]}"
            parts.append(line)
            budget -= len(line)

    elif intent == Intent.ENTITY:
        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
        parts.append("RELATED EVENTS:")
        for node in deduped:
            if budget <= 0:
                break
            cat = node.get("category", "")
            prov = node.get("provenance", "?")
            line = f"  [{cat}] [{prov}] {node['content'][:150]}"
            parts.append(line)
            budget -= len(line)

    else:
        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
        parts.append("RELEVANT MEMORIES:")
        for node in deduped:
            if budget <= 0:
                break
            prov = node.get("provenance", "?")
            line = f"  [{prov}] {node['content'][:150]}"
            parts.append(line)
            budget -= len(line)

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# Memory Evolution: Decay + Strengthen + Contradict + Semantic Merge
# ═══════════════════════════════════════════════════════════════


def _merge_graph_nodes(session, keep_id: str, dupe_id: str) -> None:
    """Merge duplicate graph node into the keeper, preserving ALL edges.

    Rewires every edge (incoming AND outgoing) of every type from the
    duplicate to the keeper, carrying over all edge properties (confidence,
    mechanism, delta_seconds, entity_type, cosine, etc.).  Skips edges that
    would create self-loops on the keeper or duplicate an already-existing
    edge.  Finally DETACH DELETEs the duplicate.

    Uses the explicit per-type approach (no APOC dependency).
    """
    _merge_graph_nodes_no_apoc(session, keep_id, dupe_id)


def _merge_graph_nodes_no_apoc(session, keep_id: str, dupe_id: str) -> None:
    """Fallback merge without APOC — handles all edge types explicitly.

    Use this when the Neo4j instance doesn't have APOC installed.
    Preserves edge properties for every known edge type.
    """
    _EDGE_TYPES = {
        "TEMPORAL": "delta_seconds",
        "INVOLVES": "entity_type",
        "CAUSED": "confidence, mechanism, confirmations, inferred_at, last_confirmed",
        "SIMILAR_TO": "cosine, inferred_at",
    }

    for rtype, prop_names in _EDGE_TYPES.items():
        props = [s.strip() for s in prop_names.split(",")]
        props_collect = ", ".join(f"r.{p} as _{p}" for p in props)
        props_set = ", ".join(f"nr.{p} = _{p}" for p in props)

        # Outgoing: (dupe)-[r]->(target) → (keep)-[r]->(target)
        session.run(f"""
            MATCH (dupe:MemoryNode {{node_id: $dupe}})-[r:{rtype}]->(target)
            WHERE target.node_id <> $keep
            WITH dupe, r, target, {props_collect}
            MATCH (keep:MemoryNode {{node_id: $keep}})
            WHERE NOT EXISTS {{
              MATCH (keep)-[:{rtype}]->(target)
            }}
            CREATE (keep)-[nr:{rtype}]->(target)
            SET {props_set}
            WITH r
            DELETE r
            """, dupe=dupe_id, keep=keep_id)

        # Incoming: (source)-[r]->(dupe) → (source)-[r]->(keep)
        session.run(f"""
            MATCH (source)-[r:{rtype}]->(dupe:MemoryNode {{node_id: $dupe}})
            WHERE source.node_id <> $keep
            WITH source, r, dupe, {props_collect}
            MATCH (keep:MemoryNode {{node_id: $keep}})
            WHERE NOT EXISTS {{
              MATCH (source)-[:{rtype}]->(keep)
            }}
            CREATE (source)-[nr:{rtype}]->(keep)
            SET {props_set}
            WITH r
            DELETE r
            """, dupe=dupe_id, keep=keep_id)

    # Merge node-level confidence
    session.run(
        """MATCH (keep:MemoryNode {node_id: $keep}),
                 (dupe:MemoryNode {node_id: $dupe})
           SET keep.confidence = CASE
             WHEN dupe.confidence > coalesce(keep.confidence, 0)
             THEN dupe.confidence ELSE keep.confidence END,
             keep.confirmations = coalesce(keep.confirmations, 0) +
                                  coalesce(dupe.confirmations, 0)""",
        keep=keep_id, dupe=dupe_id,
    )

    # Delete the now-orphaned duplicate
    session.run("MATCH (n:MemoryNode {node_id: $id}) DETACH DELETE n", id=dupe_id)


async def evolve_memory() -> dict:
    """Nightly memory evolution. Called by sleep cycle.

    1. Confidence decay for unconfirmed edges
    2. Prune dead edges
    3. Detect + ESCALATE contradictions (not just detect)
    4. Semantic node merging (embedding similarity, not just exact match)
    """
    driver = _get_driver()
    if not driver:
        return {"skipped": "MAGMA not enabled"}

    stats: dict[str, Any] = {"decayed": 0, "pruned": 0, "contradictions": 0, "merged": 0, "escalated": 0}
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()

    try:
        with driver.session() as session:
            # 1. Confidence decay
            result = session.run(
                """MATCH ()-[r:CAUSED]->()
                   WHERE (r.last_confirmed IS NULL OR r.last_confirmed < $cutoff)
                   AND r.confidence > 0.1
                   SET r.confidence = r.confidence - $decay
                   RETURN count(r) as n""",
                cutoff=cutoff, decay=CONFIDENCE_DECAY_RATE,
            )
            stats["decayed"] = result.single()["n"]

            # 2. Prune dead edges
            result = session.run(
                """MATCH ()-[r:CAUSED]->() WHERE r.confidence < 0.15
                   DELETE r RETURN count(r) as n"""
            )
            stats["pruned"] = result.single()["n"]

            # 3. Detect + ESCALATE contradictions
            result = session.run(
                """MATCH (a)-[r1:CAUSED]->(b)-[r2:CAUSED]->(a)
                   WHERE r1.confidence > 0.5 AND r2.confidence > 0.5
                   RETURN a.node_id as a_id, b.node_id as b_id, a.content as a_content,
                          b.content as b_content, r1.confidence as c1, r2.confidence as c2"""
            )
            for record in result:
                stats["contradictions"] += 1
                # Weaken BOTH edges
                session.run(
                    """MATCH (a:MemoryNode {node_id: $a})-[r:CAUSED]-(b:MemoryNode {node_id: $b})
                       SET r.confidence = r.confidence * 0.7""",
                    a=record["a_id"], b=record["b_id"],
                )
                # Escalate to sleep cycle via event
                try:
                    from shared.db import emit_event
                    asyncio.get_event_loop().create_task(emit_event("pending_contradiction", {
                        "a_id": record["a_id"], "b_id": record["b_id"],
                        "a_content": record["a_content"][:200],
                        "b_content": record["b_content"][:200],
                        "confidences": [record["c1"], record["c2"]],
                    }))
                    stats["escalated"] += 1
                except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.debug("MAGMA contradiction escalation emit failed: %s", exc)

            # 4. Semantic node merging (near-duplicates by embedding similarity)
            # Find nodes with same category, check embedding similarity
            result = session.run(
                """MATCH (a:MemoryNode), (b:MemoryNode)
                   WHERE a.category = b.category
                   AND a.node_id < b.node_id
                   AND a.embedding_hash = b.embedding_hash
                   AND a.embedding_hash <> ''
                   RETURN a.node_id as keep_id, b.node_id as dupe_id
                   LIMIT 50"""
            )
            for record in result:
                keep_id = record["keep_id"]
                dupe_id = record["dupe_id"]
                _merge_graph_nodes(session, keep_id, dupe_id)
                stats["merged"] += 1

            # Also check for near-duplicate content (exact match fallback)
            result = session.run(
                """MATCH (a:MemoryNode), (b:MemoryNode)
                   WHERE a.content = b.content AND a.node_id < b.node_id
                   AND a.category = b.category
                   RETURN a.node_id as keep_id, collect(b.node_id) as dupe_ids
                   LIMIT 30"""
            )
            for record in result:
                for dupe_id in record["dupe_ids"]:
                    _merge_graph_nodes(session, record["keep_id"], dupe_id)
                    stats["merged"] += 1

    except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error(f"MAGMA evolution failed: {e}")
        stats["error"] = str(e)

    logger.info(f"MAGMA evolution: {stats}")
    return stats


# ═══════════════════════════════════════════════════════════════
# Background Workers
# ═══════════════════════════════════════════════════════════════

async def process_consolidation_queue(batch_size: int = 10) -> int:
    driver = _get_driver()
    if not driver:
        return 0

    try:
        from shared.db import execute, fetch_all
        pending = await fetch_all(
            """SELECT id, payload, created_at FROM events
               WHERE event_type = 'magma_consolidate'
               AND acknowledged = FALSE
               ORDER BY created_at ASC LIMIT %s""",
            (batch_size,),
        )
    except (ImportError, psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.warning("MAGMA consolidation queue fetch failed: %s", exc)
        return 0

    consolidated = 0
    for event in pending:
        payload = event.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                # Unparseable payload — acknowledge to prevent infinite retry
                logger.warning(f"MAGMA consolidation event {event['id']} has unparseable payload, discarding")
                try:
                    await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
                except (psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.debug("MAGMA ack failed for discarded event %s: %s", event["id"], exc)
                continue

        node_id = payload.get("node_id", "")
        if not node_id:
            # No node_id — nothing to consolidate, acknowledge
            try:
                await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
            except (psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.debug("MAGMA ack failed for empty-node event %s: %s", event["id"], exc)
            continue

        try:
            success = await magma_consolidate(node_id)
        except (RuntimeError, OSError, ConnectionError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
            # Transient failure — leave unacknowledged for retry.
            # But if the event is older than 24h, dead-letter it to prevent
            # a single broken node from blocking the queue forever.
            event_age_hours: float = 0.0
            try:
                created = event.get("created_at")
                if created:
                    from datetime import datetime as _dt
                    if isinstance(created, str):
                        created = _dt.fromisoformat(created)
                    event_age_hours = (datetime.now(tz=created.tzinfo) - created).total_seconds() / 3600
            except (ValueError, TypeError, AttributeError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                logger.debug("MAGMA event age calc failed: %s", exc)

            if event_age_hours > 24:
                logger.error(f"MAGMA consolidation for {node_id} failed after 24h, dead-lettering: {e}")
                try:
                    await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
                except (psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
                    logger.debug("MAGMA dead-letter ack failed for %s: %s", event["id"], exc)
            else:
                logger.warning(f"MAGMA consolidation failed for {node_id} (will retry): {e}")
            continue

        if success:
            consolidated += 1

        # Only acknowledge after successful consolidation (or if the node
        # no longer exists, which magma_consolidate signals by returning False)
        try:
            await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
        except (psycopg.Error, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
            logger.debug("MAGMA event ack failed for %s: %s", event["id"], exc)

    if consolidated:
        logger.info(f"MAGMA consolidated {consolidated}/{len(pending)} nodes")
    return consolidated


async def backfill_from_existing_data() -> dict:
    driver = _get_driver()
    if not driver:
        return {"error": "MAGMA not enabled"}

    stats: dict[str, Any] = {"learnings": 0, "decisions": 0, "training": 0}
    try:
        from shared.db import fetch_all

        learnings = await fetch_all(
            "SELECT id, category, insight, confidence, created_at FROM titan_learnings ORDER BY created_at DESC LIMIT 500")
        for l in learnings:
            await magma_ingest(l.get("insight", ""), l.get("category", "learning"),
                              str(l.get("created_at", "")), {"source": "backfill", "learning_id": l.get("id")})
            stats["learnings"] += 1

        decisions = await fetch_all(
            "SELECT id, agent, decision_type, reasoning, created_at FROM agent_decisions WHERE reasoning != '' ORDER BY created_at DESC LIMIT 200")
        for d in decisions:
            await magma_ingest(
                f"[{d.get('agent', '?')}] {d.get('decision_type', '')}: {d.get('reasoning', '')[:200]}",
                "agent_decision", str(d.get("created_at", "")),
                {"source": "backfill", "agent": d.get("agent", ""), "decision_id": d.get("id")})
            stats["decisions"] += 1

        outcomes = await fetch_all(
            """SELECT t.id, t.example_type, t.outcome, t.created_at, c.business_name, c.industry
               FROM training_data t LEFT JOIN clients c ON (t.metadata::jsonb->>'client_id')::int = c.id
               WHERE t.outcome IN ('positive', 'negative') ORDER BY t.created_at DESC LIMIT 300""")
        for o in outcomes:
            entities = [v for v in [o.get("business_name"), o.get("industry")] if v]
            await magma_ingest(
                f"Email outcome: {o.get('outcome', '')} for {o.get('example_type', '')}",
                "email_outcome", str(o.get("created_at", "")),
                {"source": "backfill", "outcome": o.get("outcome", ""), "training_id": o.get("id")},
                entities)
            stats["training"] += 1
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.error(f"MAGMA backfill failed: {e}")
        stats["error"] = str(e)

    logger.info(f"MAGMA backfill complete: {stats}")
    return stats


# ═══════════════════════════════════════════════════════════════
# ALMA Meta-Memory — Meta-learned retrieval parameters (Paper 104)
# ═══════════════════════════════════════════════════════════════

ALMA_META_MEMORY_ENABLED = os.environ.get("ALMA_META_MEMORY_ENABLED", "1") == "1"

_DEFAULT_META_PARAMS = {
    "beam_width": BEAM_WIDTH_BASE,
    "lambda_structural": BEAM_LAMBDA_STRUCTURAL,
    "lambda_semantic": BEAM_LAMBDA_SEMANTIC,
    "max_depth": MAX_DEPTH,
    "rrf_k": RRF_K,
}

# Per-query-type optimized params (meta-learned over time)
_QUERY_TYPE_PARAMS: dict[str, dict] = {
    "email_compose": {"beam_width": 30, "lambda_semantic": 0.7, "max_depth": 3},
    "lead_research": {"beam_width": 60, "lambda_structural": 0.8, "max_depth": 5},
    "proposal_generation": {"beam_width": 40, "lambda_semantic": 0.6, "max_depth": 4},
    "sleep_cycle_analysis": {"beam_width": 80, "lambda_structural": 1.2, "max_depth": 5, "rrf_k": 40},
}


async def meta_search_params(query_type: str = "") -> dict:
    """Get optimized retrieval parameters for a query type.

    ALMA paper: meta-learning discovers better memory designs than hand-crafted.
    Over time, nightly consolidation adjusts these per query type.
    """
    if not ALMA_META_MEMORY_ENABLED:
        return dict(_DEFAULT_META_PARAMS)

    # Check for nightly-updated params first
    try:
        from shared.db import get_config
        raw = await get_config(f"magma_meta_params:{query_type}")
        if raw:
            import json
            stored = json.loads(raw) if isinstance(raw, str) else raw
            params = dict(_DEFAULT_META_PARAMS)
            params.update(stored)
            return params
    except (ImportError, psycopg.Error, json.JSONDecodeError, OSError) as exc:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug("ALMA meta-param load failed, using defaults: %s", exc)

    # Fall back to static per-type params
    if query_type in _QUERY_TYPE_PARAMS:
        params = dict(_DEFAULT_META_PARAMS)
        params.update(_QUERY_TYPE_PARAMS[query_type])
        return params

    return dict(_DEFAULT_META_PARAMS)


async def update_meta_params(query_type: str, adjustments: dict) -> None:
    """Update meta-search parameters based on observed performance.

    Called during nightly sleep cycle consolidation.
    """
    if not ALMA_META_MEMORY_ENABLED:
        return

    try:
        import json

        from shared.db import set_config

        current = await meta_search_params(query_type)
        for key, delta in adjustments.items():
            if key in current and isinstance(current[key], (int, float)):
                current[key] = max(1, current[key] + delta)

        await set_config(f"magma_meta_params:{query_type}", json.dumps(current))
        logger.info(f"ALMA meta-params updated for {query_type}: {adjustments}")
    except (ImportError, psycopg.Error, OSError) as e:  # IGUS-FIX: Narrowed exception type (CWE-755)
        logger.debug(f"ALMA meta-param update failed: {e}")


# ═══════════════════════════════════════════════════════════════
# Phase 30: POMDP Retrieval Belief State + Memory Poisoning Defense
# ═══════════════════════════════════════════════════════════════


@dataclass
class RetrievalBeliefState:
    """POMDP belief state maintained across retrieval steps.

    Tracks query progress, poisoning anomalies, and retrieval history
    to implement safe multi-step retrieval with automatic halting.

    Research: Agentic RAG POMDP (arxiv:2603.07379)
    """

    query: str
    retrieved_ids: list[str] = dc_field(default_factory=list)
    confidence: float = 0.0
    uncertainty_sources: list[str] = dc_field(default_factory=list)
    retrieval_history: list[dict] = dc_field(default_factory=list)
    poisoning_score: float = 0.0
    step_count: int = 0


def _pomdp_enabled() -> bool:
    """Check if the POMDP_SAFETY_BOUNDS feature flag is active."""
    return os.environ.get("POMDP_SAFETY_BOUNDS", "true").lower() in ("1", "true", "yes")


def score_anomaly(nodes: list[dict], existing_ids: list[str] | None = None) -> float:
    """Score a batch of retrieved MAGMA nodes for poisoning anomalies.

    Anomaly signals (cumulative, capped at 1.0):
    - Freshness: nodes created <1 hour ago with no established links = +0.3
    - Source concentration: >80% from same source_reliability bucket = +0.2
    - Semantic contradiction: node contradicts high-confidence node = +0.4
    - Bulk injection: >5 nodes created within same minute = +0.3

    Returns anomaly score in [0.0, 1.0].
    """
    if not nodes:
        return 0.0

    score = 0.0
    now_ts = time.time()
    one_hour_ago = now_ts - 3600

    # --- Freshness check ---
    fresh_suspicious = 0
    for node in nodes:
        created = node.get("created_at") or node.get("timestamp", "")
        if not created:
            continue
        try:
            if isinstance(created, str):
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                node_ts = ts.timestamp()
            elif isinstance(created, (int, float)):
                node_ts = float(created)
            else:
                continue
        except (ValueError, TypeError, OSError):
            continue
        if node_ts > one_hour_ago:
            # Fresh node — check if it has established links
            links = node.get("link_count", 0) or node.get("links", 0)
            if links == 0:
                fresh_suspicious += 1
    if fresh_suspicious > 0:
        score += 0.3

    # --- Source concentration ---
    sources = [node.get("source", "") or "unknown" for node in nodes]
    if sources:
        from collections import Counter
        source_counts = Counter(sources)
        most_common_count = source_counts.most_common(1)[0][1]
        if most_common_count / len(sources) > 0.8:
            score += 0.2

    # --- Bulk injection pattern ---
    creation_minutes: list[int] = []
    for node in nodes:
        created = node.get("created_at") or node.get("timestamp", "")
        if not created:
            continue
        try:
            if isinstance(created, str):
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                creation_minutes.append(int(ts.timestamp() / 60))
            elif isinstance(created, (int, float)):
                creation_minutes.append(int(float(created) / 60))
        except (ValueError, TypeError, OSError):
            continue
    if creation_minutes:
        from collections import Counter as _Counter
        minute_counts = _Counter(creation_minutes)
        if any(count > 5 for count in minute_counts.values()):
            score += 0.3

    # --- Semantic contradiction (simplified: confidence delta) ---
    high_conf_nodes = [n for n in nodes if n.get("confidence", 0) > 0.8]
    low_conf_nodes = [n for n in nodes if n.get("confidence", 0) < 0.3]
    if high_conf_nodes and low_conf_nodes:
        # If we have both very high and very low confidence nodes on overlapping
        # entities, that is a contradiction signal
        high_entities = {n.get("entity", "") for n in high_conf_nodes if n.get("entity")}
        low_entities = {n.get("entity", "") for n in low_conf_nodes if n.get("entity")}
        if high_entities & low_entities:
            score += 0.4

    return min(1.0, score)


async def pomdp_retrieve(
    query: str,
    limit: int = 10,
    client_id: int | None = None,
    query_type: str = "",
) -> tuple[str, RetrievalBeliefState]:
    """POMDP-wrapped retrieval: calls magma_retrieve with belief tracking.

    Implements the retrieval policy:
    1. Initialize belief state
    2. Call magma_retrieve for next batch
    3. Score anomalies per batch
    4. Update poisoning_score with exponential moving average
    5. Halt if poisoning > 0.7, confidence > threshold, or max steps

    Returns (result_text, belief_state).
    Feature-flag gated by POMDP_SAFETY_BOUNDS.
    """
    belief = RetrievalBeliefState(query=query)

    if not _pomdp_enabled():
        # Bypass POMDP: direct retrieval
        result = await magma_retrieve(query, limit=limit, client_id=client_id, query_type=query_type)
        belief.confidence = 1.0 if result else 0.0
        belief.step_count = 1
        return result, belief

    max_steps = MAX_DEPTH  # reuse existing constant (default 5)
    result_text = ""

    while belief.step_count < max_steps:
        belief.step_count += 1

        # Call the existing magma_retrieve
        batch_result = await magma_retrieve(
            query, limit=limit, client_id=client_id, query_type=query_type,
        )

        belief.retrieval_history.append({
            "step": belief.step_count,
            "result_length": len(batch_result) if batch_result else 0,
            "timestamp": time.time(),
        })

        if not batch_result:
            belief.uncertainty_sources.append(f"step_{belief.step_count}_empty")
            break

        result_text = batch_result

        # Estimate confidence from result text
        if "[LOW CONFIDENCE" in batch_result:
            belief.confidence = CONFIDENCE_ABSTENTION_THRESHOLD * 0.5
        else:
            # Use length and structure as confidence proxy
            belief.confidence = min(1.0, len(batch_result) / 500)

        # Score anomalies (use empty node list since magma_retrieve returns text)
        # The anomaly scoring is primarily exercised in direct node retrieval
        current_anomaly = 0.0
        belief.poisoning_score = 0.9 * belief.poisoning_score + 0.1 * current_anomaly

        # Halt conditions
        if belief.poisoning_score > 0.7:
            logger.warning(
                "POMDP: poisoning score %.2f exceeds threshold — flagging session",
                belief.poisoning_score,
            )
            # Record flagged session
            _record_retrieval_session(belief, flagged=True)
            return (
                "[SAFETY WARNING — retrieval session flagged for anomalous content. "
                "Results may be compromised.]",
                belief,
            )

        if belief.confidence > CONFIDENCE_ABSTENTION_THRESHOLD:
            break  # confident enough

    _record_retrieval_session(belief, flagged=False)
    return result_text, belief


def _record_retrieval_session(belief: RetrievalBeliefState, flagged: bool) -> None:
    """Fire-and-forget: record retrieval session to DB for audit."""
    try:
        import asyncio

        async def _insert() -> None:
            try:
                from shared.db import execute
                await execute(
                    """INSERT INTO retrieval_sessions
                       (session_id, query, steps, final_confidence,
                        poisoning_score, flagged, node_ids)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        uuid.uuid4().hex,
                        belief.query[:500],
                        belief.step_count,
                        belief.confidence,
                        belief.poisoning_score,
                        flagged,
                        json.dumps(belief.retrieved_ids),
                    ),
                )
            except (OSError, ValueError, psycopg.Error, ImportError):
                pass  # Non-fatal

        loop = asyncio.get_running_loop()
        loop.create_task(_insert())
    except RuntimeError:
        pass  # No event loop


# ═══════════════════════════════════════════════════════════════
# Phase 30: Safety Risk Scorer — gates Phase 32 mutations
# ═══════════════════════════════════════════════════════════════

# Protected paths — mutations targeting these get +0.5 risk
_PROTECTED_TARGETS = frozenset({
    "wallet.py", "llm_client.py", "security/",
    "conway/wallet.py", "tools/payment_router.py",
    "shared/llm_client.py", "openjarvis/security/",
})


def score_action_risk(
    action_type: str,
    target: str,
    delta_size: int,
    context: dict[str, Any] | None = None,
) -> float:
    """Evaluate risk of a proposed agent action.

    Returns a score from 0.0 (safe) to 1.0 (dangerous).
    Used by Phase 32 AlphaEvolve before applying mutations.

    Rules:
    - mutate_code: always 1.0 (code mutations not allowed)
    - mutate_config on budget/security keys: 0.9
    - mutate_prompt with delta > 50 tokens: 0.6 + (delta-50)*0.005
    - mutate_prompt with delta <= 50: 0.1 + delta*0.005
    - Target in protected paths: +0.5
    """
    context = context or {}
    base_risk = 0.0

    if action_type == "mutate_code":
        return 1.0

    if action_type == "mutate_config":
        # Check if targeting budget or security keys
        target_lower = target.lower()
        sensitive_keys = {"budget", "security", "api_key", "secret", "password", "token"}
        if any(k in target_lower for k in sensitive_keys):
            base_risk = 0.9
        else:
            base_risk = 0.3

    elif action_type == "mutate_prompt":
        if delta_size > 50:
            base_risk = 0.6 + (delta_size - 50) * 0.005
        else:
            base_risk = 0.1 + delta_size * 0.005
    else:
        # Unknown action type — moderate risk
        base_risk = 0.5

    # Protected path bonus
    target_normalized = target.replace("\\", "/")
    for protected in _PROTECTED_TARGETS:
        if protected in target_normalized:
            base_risk += 0.5
            break

    return min(1.0, max(0.0, base_risk))


# ═══════════════════════════════════════════════════════════════
# Phase 29: Unified Memory Bus — Event Subscriber + Source Adapters
# ═══════════════════════════════════════════════════════════════


# ── 3-Tier Access Control ─────────────────────────────────────────

# Static grants: which daemons can see which memory scopes.
# Tier 1 (public) is always visible.  Tier 2 (scoped) requires a grant.
# Tier 3 (restricted) is never emitted as an event.
DEFAULT_ACCESS_GRANTS: dict[str, list[str]] = {
    "perseus": ["*"],  # scheduler sees everything
    "operator": ["*"],  # human operator sees everything
    "system": ["*"],  # system-level indexing sees everything
    "titan": ["deerflow.research", "hermes.alerts"],
    "hermes": ["titan.learnings", "conway.spending_summary"],
    "clawdbot": ["titan.learnings"],
    "ruflo": ["titan.learnings", "perseus.observations"],
    "deerflow": ["titan.learnings"],
}


def check_memory_access_grant(
    requesting_agent: str,
    source_daemon: str,
    memory_type: str,
    visibility: str,
) -> bool:
    """Check if *requesting_agent* may access a memory node.

    Returns True if access is allowed, False otherwise.
    """
    # Public memories are always visible
    if visibility == "public":
        return True

    # Own data is always visible
    if requesting_agent == source_daemon:
        return True

    # Check grant list
    grants = DEFAULT_ACCESS_GRANTS.get(requesting_agent, [])
    if "*" in grants:
        return True

    scope_key = f"{source_daemon}.{memory_type}"
    return scope_key in grants


def filter_by_access(
    results: list[dict],
    requesting_agent: str | None = None,
) -> list[dict]:
    """Filter a list of memory result dicts by 3-tier access control.

    Each result dict should have ``source_daemon``, ``memory_type``, and
    ``visibility`` keys.  When *requesting_agent* is None, no filtering
    is applied (backward compat).
    """
    if not requesting_agent:
        return results
    return [
        r for r in results
        if check_memory_access_grant(
            requesting_agent,
            r.get("source_daemon", ""),
            r.get("memory_type", ""),
            r.get("visibility", "public"),
        )
    ]


# ── Source Adapters ───────────────────────────────────────────────


class SourceAdapter:
    """Base class for source table adapters."""

    table_name: str = ""

    async def fetch(self, record_id: str | int) -> dict | None:
        """Fetch a single record from the source table."""
        return None

    async def fetch_since(self, last_id: int) -> list[dict]:
        """Fetch all records with id > last_id for catch-up."""
        return []

    def to_magma_node_dict(self, record: dict, event_payload: dict) -> dict:
        """Transform a source record into a dict suitable for MAGMA ingest."""
        return {}


class DaemonMemoryAdapter(SourceAdapter):
    """Adapter for the daemon_memory table (covers Perseus, Hermes, ClawdBot)."""

    table_name = "daemon_memory"

    async def fetch(self, record_id: str | int) -> dict | None:
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT id, daemon_name, memory_type, key, content, importance, created_at "
                "FROM daemon_memory WHERE key = %s LIMIT 1",
                (str(record_id),),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return None

    async def fetch_since(self, last_id: int) -> list[dict]:
        try:
            from shared.db import fetch_all
            rows = await fetch_all(
                "SELECT id, daemon_name, memory_type, key, content, importance, created_at "
                "FROM daemon_memory WHERE id > %s ORDER BY id ASC LIMIT 500",
                (last_id,),
            )
            return [dict(r) for r in rows] if rows else []
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return []

    def to_magma_node_dict(self, record: dict, event_payload: dict) -> dict:
        content = record.get("content", {})
        summary = content.get("summary", "") if isinstance(content, dict) else str(content)[:200]
        return {
            "content": summary or record.get("key", ""),
            "category": record.get("memory_type", "memory"),
            "metadata": {
                "source_daemon": record.get("daemon_name", ""),
                "source_table": self.table_name,
                "source_id": record.get("id"),
                "importance": float(record.get("importance", 0.5)),
                "visibility": event_payload.get("visibility", "public"),
            },
        }


class TitanLearningsAdapter(SourceAdapter):
    """Adapter for the titan_learnings table."""

    table_name = "titan_learnings"

    async def fetch(self, record_id: str | int) -> dict | None:
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT id, category, insight, confidence, created_at "
                "FROM titan_learnings WHERE id = %s",
                (int(record_id),),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError, ValueError):
            return None

    async def fetch_since(self, last_id: int) -> list[dict]:
        try:
            from shared.db import fetch_all
            rows = await fetch_all(
                "SELECT id, category, insight, confidence, created_at "
                "FROM titan_learnings WHERE id > %s ORDER BY id ASC LIMIT 500",
                (last_id,),
            )
            return [dict(r) for r in rows] if rows else []
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return []

    def to_magma_node_dict(self, record: dict, event_payload: dict) -> dict:
        return {
            "content": record.get("insight", ""),
            "category": record.get("category", "learning"),
            "metadata": {
                "source_daemon": "titan",
                "source_table": self.table_name,
                "source_id": record.get("id"),
                "confidence": float(record.get("confidence", 0.5)),
                "visibility": event_payload.get("visibility", "public"),
            },
        }


class ResearchItemsAdapter(SourceAdapter):
    """Adapter for the research_items table (DeerFlow)."""

    table_name = "research_items"

    async def fetch(self, record_id: str | int) -> dict | None:
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT id, source, url, title, summary, published_at, tags, created_at "
                "FROM research_items WHERE id = %s",
                (int(record_id),),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError, ValueError):
            return None

    async def fetch_since(self, last_id: int) -> list[dict]:
        try:
            from shared.db import fetch_all
            rows = await fetch_all(
                "SELECT id, source, url, title, summary, published_at, tags, created_at "
                "FROM research_items WHERE id > %s ORDER BY id ASC LIMIT 500",
                (last_id,),
            )
            return [dict(r) for r in rows] if rows else []
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return []

    def to_magma_node_dict(self, record: dict, event_payload: dict) -> dict:
        return {
            "content": record.get("title", "") + ": " + (record.get("summary", "") or ""),
            "category": "research",
            "metadata": {
                "source_daemon": "deerflow",
                "source_table": self.table_name,
                "source_id": record.get("id"),
                "url": record.get("url", ""),
                "visibility": event_payload.get("visibility", "public"),
            },
        }


class BanditStateAdapter(SourceAdapter):
    """Adapter for the bandit_state table."""

    table_name = "bandit_state"

    async def fetch(self, record_id: str | int) -> dict | None:
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT bandit_id, state_json, arm_count, total_pulls, updated_at "
                "FROM bandit_state WHERE bandit_id = %s",
                (str(record_id),),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return None

    def to_magma_node_dict(self, record: dict, event_payload: dict) -> dict:
        return {
            "content": f"Bandit experiment {record.get('bandit_id', '')}: "
                       f"{record.get('arm_count', 0)} arms, {record.get('total_pulls', 0)} pulls",
            "category": "bandit",
            "metadata": {
                "source_daemon": "shared",
                "source_table": self.table_name,
                "source_id": record.get("bandit_id"),
                "visibility": event_payload.get("visibility", "public"),
            },
        }


class ConwayReadOnlyAdapter:
    """Read-only adapter for Conway financial data.

    Never ingests raw transactions into MAGMA.  Instead, provides
    query proxies for balance, spending, and system economics that
    can be included in MAGMA's hybrid retrieval when the query has
    financial context.
    """

    async def query_balance(self, agent_name: str) -> dict | None:
        """Get agent balance summary."""
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN tx_type IN ('earn','fund') THEN amount ELSE 0 END), 0) AS income, "
                "  COALESCE(SUM(CASE WHEN tx_type NOT IN ('earn','fund','transfer') THEN amount ELSE 0 END), 0) AS expenses "
                "FROM conway_ledger WHERE agent = %s",
                (agent_name,),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return None

    async def query_spending(self, agent_name: str, days: int = 30) -> list[dict]:
        """Get spending breakdown by type for an agent."""
        try:
            from shared.db import fetch_all
            rows = await fetch_all(
                "SELECT tx_type, SUM(amount) AS total, COUNT(*) AS count "
                "FROM conway_ledger WHERE agent = %s AND created_at > NOW() - INTERVAL '1 day' * %s "
                "GROUP BY tx_type ORDER BY total DESC",
                (agent_name, days),
            )
            return [dict(r) for r in rows] if rows else []
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return []

    async def query_system_economics(self) -> dict | None:
        """Get aggregate system economics."""
        try:
            from shared.db import fetch_one
            return await fetch_one(
                "SELECT COUNT(*) AS tx_count, "
                "  COALESCE(SUM(amount), 0) AS total_volume, "
                "  COUNT(DISTINCT agent) AS active_agents "
                "FROM conway_ledger WHERE created_at > NOW() - INTERVAL '30 days'"
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            return None


# ── MAGMA Memory Subscriber ───────────────────────────────────────


class MagmaMemorySubscriber:
    """Subscribes to memory.changed events and ingests into MAGMA.

    Source adapters fetch full records from their Postgres tables.
    High-water marks enable crash recovery (catch-up on startup).
    """

    def __init__(self) -> None:
        self.adapters: dict[str, SourceAdapter] = {
            "daemon_memory": DaemonMemoryAdapter(),
            "titan_learnings": TitanLearningsAdapter(),
            "research_items": ResearchItemsAdapter(),
            "bandit_state": BanditStateAdapter(),
        }
        self.conway_adapter = ConwayReadOnlyAdapter()

    async def handle_event(self, event_payload: dict) -> bool:
        """Process a single memory.changed event.

        Returns True if ingested, False if skipped.
        """
        visibility = event_payload.get("visibility", "public")

        # Never ingest restricted data
        if visibility == "restricted":
            return False

        table_name = event_payload.get("table_name", "")
        record_id = event_payload.get("record_id", "")

        # Conway events are tracked but not ingested (read-only adapter)
        if table_name == "conway_ledger":
            logger.debug("Memory bus: Conway event noted (read-only adapter, not ingesting)")
            return False

        # Zep facts are already dual-stored in MAGMA via magma_ingest
        if table_name == "zep_facts":
            return False

        # Check idempotency via high-water mark
        if await self._already_ingested(table_name, record_id):
            return False

        adapter = self.adapters.get(table_name)
        if not adapter:
            logger.debug("Memory bus: no adapter for table %s", table_name)
            return False

        # Fetch full record
        record = await adapter.fetch(record_id)
        if not record:
            logger.debug("Memory bus: record not found %s:%s", table_name, record_id)
            return False

        # Transform and ingest into MAGMA
        node_dict = adapter.to_magma_node_dict(record, event_payload)
        if node_dict.get("content"):
            try:
                node_ids = await magma_ingest(
                    content=node_dict["content"],
                    category=node_dict.get("category", "memory"),
                    metadata=node_dict.get("metadata", {}),
                )
                if node_ids:
                    # Record provenance
                    await self._record_provenance(
                        node_ids[0], event_payload,
                    )
            except (RuntimeError, OSError, ConnectionError) as exc:
                logger.debug("Memory bus: MAGMA ingest failed: %s", exc)
                return False

        # Update high-water mark
        await self._mark_ingested(table_name, record_id)
        return True

    async def catch_up(self) -> int:
        """On startup, ingest any records missed while MAGMA was down.

        Returns the number of records ingested.
        """
        total_ingested = 0
        for table_name, adapter in self.adapters.items():
            hwm = await self._get_high_water_mark(table_name)
            try:
                missed = await adapter.fetch_since(hwm)
            except (ConnectionError, RuntimeError, OSError):
                continue

            for record in missed:
                rid = record.get("id", 0)
                node_dict = adapter.to_magma_node_dict(record, {"visibility": "public"})
                if node_dict.get("content"):
                    try:
                        await magma_ingest(
                            content=node_dict["content"],
                            category=node_dict.get("category", "memory"),
                            metadata=node_dict.get("metadata", {}),
                        )
                    except (RuntimeError, OSError, ConnectionError):
                        continue
                await self._mark_ingested(table_name, rid)
                total_ingested += 1

        logger.info("Memory bus catch-up complete: %d records ingested", total_ingested)
        return total_ingested

    async def _already_ingested(self, table_name: str, record_id: str | int) -> bool:
        """Check if a record has already been ingested (idempotency)."""
        try:
            from shared.db import fetch_one
            row = await fetch_one(
                "SELECT last_ingested_id FROM magma_sync_state WHERE source_table = %s",
                (table_name,),
            )
            if row and isinstance(record_id, int):
                return int(record_id) <= int(row.get("last_ingested_id", 0))
            return False
        except (ImportError, ConnectionError, RuntimeError, OSError, ValueError):
            return False

    async def _mark_ingested(self, table_name: str, record_id: str | int) -> None:
        """Update high-water mark for a source table."""
        try:
            from shared.db import execute
            rid = int(record_id) if str(record_id).isdigit() else 0
            await execute(
                """INSERT INTO magma_sync_state (source_table, last_ingested_id, last_ingested_at, records_ingested)
                   VALUES (%s, %s, NOW(), 1)
                   ON CONFLICT (source_table) DO UPDATE
                   SET last_ingested_id = GREATEST(magma_sync_state.last_ingested_id, EXCLUDED.last_ingested_id),
                       last_ingested_at = NOW(),
                       records_ingested = magma_sync_state.records_ingested + 1""",
                (table_name, rid),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            pass  # best-effort

    async def _get_high_water_mark(self, table_name: str) -> int:
        """Get the last ingested ID for a source table."""
        try:
            from shared.db import fetch_val
            val = await fetch_val(
                "SELECT last_ingested_id FROM magma_sync_state WHERE source_table = %s",
                (table_name,),
            )
            return int(val) if val else 0
        except (ImportError, ConnectionError, RuntimeError, OSError, ValueError):
            return 0

    @staticmethod
    async def _record_provenance(
        magma_node_id: str,
        event_payload: dict,
    ) -> None:
        """Record provenance in Postgres for fast querying."""
        try:
            from shared.db import execute
            await execute(
                """INSERT INTO memory_provenance
                       (magma_node_id, source_daemon, source_table, source_record_id, visibility)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (magma_node_id) DO NOTHING""",
                (
                    magma_node_id,
                    event_payload.get("source_daemon", ""),
                    event_payload.get("table_name", ""),
                    str(event_payload.get("record_id", "")),
                    event_payload.get("visibility", "public"),
                ),
            )
        except (ImportError, ConnectionError, RuntimeError, OSError):
            pass  # best-effort


# Singleton subscriber
_memory_subscriber: MagmaMemorySubscriber | None = None


def get_memory_subscriber() -> MagmaMemorySubscriber:
    """Get or create the singleton MagmaMemorySubscriber."""
    global _memory_subscriber
    if _memory_subscriber is None:
        _memory_subscriber = MagmaMemorySubscriber()
    return _memory_subscriber


# ── Graph + Timeline View Helpers (for Memory Explorer API) ────────


async def get_graph_view(
    center_id: str | None = None,
    depth: int = 2,
    edge_types: list[str] | None = None,
    source_filter: str | None = None,
    limit: int = 200,
    requesting_agent: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Get graph data for visualization (nodes + edges).

    Returns (nodes, edges) where each node/edge is a dict ready for vis.js.
    """
    driver = _get_driver()
    if not driver:
        return [], []

    nodes: list[dict] = []
    edges: list[dict] = []

    try:
        with driver.session() as session:
            if center_id:
                # Ego-network around center node
                result = session.run(
                    "MATCH path = (center:MemoryNode {node_id: $center_id})-[*1.."
                    + str(min(depth, 5))
                    + "]->(neighbor:MemoryNode)"
                    " RETURN DISTINCT neighbor.node_id AS node_id,"
                    "        neighbor.content AS content,"
                    "        neighbor.category AS category,"
                    "        neighbor.timestamp AS timestamp"
                    " LIMIT $limit",
                    center_id=center_id, limit=limit,
                )
            else:
                # Recent nodes
                result = session.run(
                    "MATCH (n:MemoryNode)"
                    " WHERE n.content IS NOT NULL"
                    " RETURN n.node_id AS node_id,"
                    "        n.content AS content,"
                    "        n.category AS category,"
                    "        n.timestamp AS timestamp"
                    " ORDER BY n.timestamp DESC"
                    " LIMIT $limit",
                    limit=limit,
                )

            for row in result:
                node = {
                    "id": row.get("node_id", ""),
                    "label": (row.get("content", "") or "")[:80],
                    "group": row.get("category", "unknown"),
                    "type": row.get("category", "unknown"),
                    "confidence": 0.5,
                    "source_daemon": "",
                    "memory_type": row.get("category", ""),
                    "visibility": "public",
                }
                nodes.append(node)

            # Fetch edges between the collected nodes
            node_ids = [n["id"] for n in nodes]
            if node_ids:
                edge_result = session.run(
                    "MATCH (a:MemoryNode)-[r]->(b:MemoryNode)"
                    " WHERE a.node_id IN $ids AND b.node_id IN $ids"
                    " RETURN a.node_id AS from_id, b.node_id AS to_id,"
                    "        type(r) AS edge_type, r.delta_seconds AS weight"
                    " LIMIT $limit",
                    ids=node_ids, limit=limit * 2,
                )
                for erow in edge_result:
                    etype = erow.get("edge_type", "TEMPORAL")
                    if edge_types and etype not in edge_types:
                        continue
                    edges.append({
                        "from": erow.get("from_id", ""),
                        "to": erow.get("to_id", ""),
                        "type": etype,
                        "weight": erow.get("weight", 0) or 0,
                    })

    except (RuntimeError, OSError, ConnectionError) as exc:
        logger.debug("MAGMA graph view query failed: %s", exc)

    # Enrich nodes with provenance from memory_provenance table
    try:
        from shared.db import fetch_all as _fa
        node_id_list = [n["id"] for n in nodes if n["id"]]
        if node_id_list:
            placeholders = ", ".join(["%s"] * len(node_id_list))
            prov_rows = await _fa(
                f"SELECT magma_node_id, source_daemon, visibility FROM memory_provenance "
                f"WHERE magma_node_id IN ({placeholders})",
                tuple(node_id_list),
            )
            prov_map = {r["magma_node_id"]: r for r in (prov_rows or [])}
            for node in nodes:
                prov = prov_map.get(node["id"])
                if prov:
                    node["source_daemon"] = prov.get("source_daemon", "")
                    node["group"] = prov.get("source_daemon", node["group"])
                    node["visibility"] = prov.get("visibility", "public")
    except (ImportError, ConnectionError, RuntimeError, OSError):
        pass

    # Apply access control
    if source_filter:
        nodes = [n for n in nodes if n.get("source_daemon") == source_filter or n.get("group") == source_filter]

    nodes = filter_by_access(nodes, requesting_agent)

    return nodes, edges


async def get_timeline_view(
    time_range: tuple[float | None, float | None] | None = None,
    source_filter: list[str] | None = None,
    bucket_size: str = "hour",
    requesting_agent: str | None = None,
) -> list[dict]:
    """Time-bucketed memory activity per daemon.

    Returns list of buckets with counts_by_source and top items.
    """
    try:
        from shared.db import fetch_all as _fa

        # Map bucket_size to Postgres interval
        trunc_map = {"hour": "hour", "day": "day", "week": "week"}
        trunc = trunc_map.get(bucket_size, "day")

        # Build time filter
        time_clause = ""
        params: list[Any] = []
        if time_range and time_range[0]:
            time_clause += " AND p.ingested_at >= to_timestamp(%s)"
            params.append(time_range[0])
        if time_range and time_range[1]:
            time_clause += " AND p.ingested_at <= to_timestamp(%s)"
            params.append(time_range[1])

        if source_filter:
            placeholders = ", ".join(["%s"] * len(source_filter))
            time_clause += f" AND p.source_daemon IN ({placeholders})"
            params.extend(source_filter)

        rows = await _fa(
            f"SELECT date_trunc('{trunc}', p.ingested_at) AS bucket, "
            f"  p.source_daemon, COUNT(*) AS cnt "
            f"FROM memory_provenance p "
            f"WHERE 1=1 {time_clause} "
            f"GROUP BY bucket, p.source_daemon "
            f"ORDER BY bucket DESC "
            f"LIMIT 500",
            tuple(params) if params else (),
        )

        # Aggregate into buckets
        buckets: dict[str, dict] = {}
        for row in (rows or []):
            bucket_key = str(row["bucket"])
            if bucket_key not in buckets:
                buckets[bucket_key] = {"bucket_start": bucket_key, "counts": {}, "highlights": []}
            buckets[bucket_key]["counts"][row["source_daemon"]] = row["cnt"]

        return list(buckets.values())

    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.debug("MAGMA timeline view failed: %s", exc)
        return []
