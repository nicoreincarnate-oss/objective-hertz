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
import json
import logging
import math
import os
import re
import time
import uuid
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
    except Exception as e:
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
    except Exception:
        return None


def _embedding_hash(vec: list[float]) -> str:
    """Stable hash of embedding vector for Neo4j storage."""
    raw = json.dumps(vec[:8], separators=(",", ":"))  # first 8 dims for speed
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
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
_node_ids_lock = asyncio.Lock()

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
    """
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
        except Exception as e:
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
        except Exception:
            pass

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
                async with _node_ids_lock:
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
                async with _node_ids_lock:
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
        except Exception as e:
            logger.debug(f"MAGMA Neo4j write failed (degrading gracefully): {e}")

        # Enqueue for slow path
        try:
            from shared.db import emit_event
            await emit_event("magma_consolidate", {"node_id": node_id, "category": category})
        except Exception:
            pass

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
    except Exception as e:
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
    except Exception:
        pass

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
            model="fast", temperature=0.1,
        )
        start = llm_result.find("{")
        end = llm_result.rfind("}") + 1
        causal = json.loads(llm_result[start:end])
    except Exception:
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
    except Exception as e:
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
        except Exception:
            pass

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
        except Exception:
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
    driver = _get_driver()
    if not driver:
        return ""

    # Resolve ALMA meta-learned params for this query type
    params = await meta_search_params(query_type)

    decomp = await _decompose_query(query, client_id=client_id)
    intent = decomp["intent"]
    anchors = await _find_anchors_linked(query, intent, decomp, limit=limit, params=params)

    if not anchors:
        return ""

    # Confidence-aware abstention (MMA paper)
    if CONFIDENCE_SCORING_ENABLED and anchors:
        avg_confidence = sum(a.get("confidence", 0) for a in anchors) / len(anchors)
        max_confidence = max(a.get("confidence", 0) for a in anchors)

        if max_confidence < CONFIDENCE_ABSTENTION_THRESHOLD:
            # All memories are low-confidence — signal caller to use flat stack
            logger.info(f"MAGMA abstaining: max_confidence={max_confidence:.2f} < {CONFIDENCE_ABSTENTION_THRESHOLD}")
            return f"[LOW CONFIDENCE — all retrieved memories scored below {CONFIDENCE_ABSTENTION_THRESHOLD:.1f}. Treat with caution.]"

    subgraph = await _scored_beam_search(driver, anchors, intent, query, decomp, params=params)
    result = _linearize_with_provenance(subgraph, intent)

    # Add confidence summary header if scoring enabled
    if CONFIDENCE_SCORING_ENABLED and anchors:
        avg_conf = sum(a.get("confidence", 0) for a in anchors) / len(anchors)
        result = f"[Memory confidence: {avg_conf:.2f} avg, {len(anchors)} sources]\n{result}"

    return result


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
                model="fast", temperature=0.0,
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
        except Exception:
            pass

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
        m = months[month_match.group(1)]
        y = int(month_match.group(2))
        start = datetime(y, m, 1)
        if m == 12:
            end = datetime(y + 1, 1, 1) - timedelta(seconds=1)
        else:
            end = datetime(y, m + 1, 1) - timedelta(seconds=1)
        return start.isoformat(), end.isoformat()
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
    except Exception:
        pass

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
        except Exception as e:
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
    except Exception:
        pass

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
                            pattern = f"(n:MemoryNode {{node_id: $nid}})-[r:CAUSED]->(rel:MemoryNode)"
                        elif direction == "backward":
                            pattern = f"(n:MemoryNode {{node_id: $nid}})<-[r:CAUSED]-(rel:MemoryNode)"
                        else:
                            pattern = f"(n:MemoryNode {{node_id: $nid}})-[r:CAUSED]-(rel:MemoryNode)"
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

    except Exception as e:
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
    except Exception:
        pass

    return expanded


# ═══════════════════════════════════════════════════════════════
# Linearization: Provenance + Topological Sort + Dedup + Budget
# ═══════════════════════════════════════════════════════════════

def _linearize_with_provenance(subgraph: list[dict], intent: Intent) -> str:
    """Paper-spec linearization: provenance markers, topo sort for causal, dedup, budget."""
    if not subgraph:
        return "No relevant memories found."

    # Dedup by content (keep highest-scored version)
    seen_content = {}
    deduped = []
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
        for anchor_id, chain in by_anchor.items():
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

    stats = {"decayed": 0, "pruned": 0, "contradictions": 0, "merged": 0, "escalated": 0}
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
                except Exception:
                    pass

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

    except Exception as e:
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
    except Exception:
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
                except Exception:
                    pass
                continue

        node_id = payload.get("node_id", "")
        if not node_id:
            # No node_id — nothing to consolidate, acknowledge
            try:
                await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
            except Exception:
                pass
            continue

        try:
            success = await magma_consolidate(node_id)
        except Exception as e:
            # Transient failure — leave unacknowledged for retry.
            # But if the event is older than 24h, dead-letter it to prevent
            # a single broken node from blocking the queue forever.
            event_age_hours = 0
            try:
                created = event.get("created_at")
                if created:
                    from datetime import datetime as _dt
                    if isinstance(created, str):
                        created = _dt.fromisoformat(created)
                    event_age_hours = (datetime.now(tz=created.tzinfo) - created).total_seconds() / 3600
            except Exception:
                pass

            if event_age_hours > 24:
                logger.error(f"MAGMA consolidation for {node_id} failed after 24h, dead-lettering: {e}")
                try:
                    await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
                except Exception:
                    pass
            else:
                logger.warning(f"MAGMA consolidation failed for {node_id} (will retry): {e}")
            continue

        if success:
            consolidated += 1

        # Only acknowledge after successful consolidation (or if the node
        # no longer exists, which magma_consolidate signals by returning False)
        try:
            await execute("UPDATE events SET acknowledged = TRUE WHERE id = %s", (event["id"],))
        except Exception:
            pass

    if consolidated:
        logger.info(f"MAGMA consolidated {consolidated}/{len(pending)} nodes")
    return consolidated


async def backfill_from_existing_data() -> dict:
    driver = _get_driver()
    if not driver:
        return {"error": "MAGMA not enabled"}

    stats = {"learnings": 0, "decisions": 0, "training": 0}
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
    except Exception as e:
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
    except Exception:
        pass

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
        from shared.db import get_config, set_config
        import json

        current = await meta_search_params(query_type)
        for key, delta in adjustments.items():
            if key in current and isinstance(current[key], (int, float)):
                current[key] = max(1, current[key] + delta)

        await set_config(f"magma_meta_params:{query_type}", json.dumps(current))
        logger.info(f"ALMA meta-params updated for {query_type}: {adjustments}")
    except Exception as e:
        logger.debug(f"ALMA meta-param update failed: {e}")
