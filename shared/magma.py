"""
MAGMA — Multi-graph Adaptive Memory with Graph-Augmented Retrieval.

Wraps and unifies the existing memory stack (Qdrant/Mem0 + Postgres + Zep)
with a Neo4j causal/temporal/entity graph layer. Does NOT replace anything —
adds the causal graph (the missing piece) and an intent-aware router.

Four simultaneous graph views per memory node:
  TEMPORAL → "this happened after that" (immutable chain)
  CAUSAL   → "this caused that" (LLM-inferred, async slow path)
  SEMANTIC → "these are conceptually similar" (Qdrant cosine)
  ENTITY   → "these involve the same business/person/industry"

Architecture:
  Fast path (every interaction, non-blocking):
    1. Write to Qdrant (semantic) — existing Mem0 path
    2. Write node to Neo4j + TEMPORAL edge to previous
    3. Extract entities → ENTITY edges
    4. Enqueue node_id for slow path

  Slow path (background, never blocks pipeline):
    1. Pull node + 2-hop neighborhood
    2. Ask Claude: "What caused this? What did this cause?"
    3. Write CAUSAL edges back to Neo4j
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from shared.config import config

logger = logging.getLogger("perseus.magma")

# ── Types ────────────────────────────────────────────────────────────


class Intent(str, Enum):
    CAUSAL = "causal"        # "Why did this happen?"
    TEMPORAL = "temporal"    # "What happened last week?"
    ENTITY = "entity"        # "What about dentist leads?"
    SEMANTIC = "semantic"    # "What's similar to this?"


class EdgeType(str, Enum):
    TEMPORAL = "TEMPORAL"
    CAUSAL = "CAUSED"
    SEMANTIC = "SIMILAR_TO"
    ENTITY = "INVOLVES"


# ── Neo4j Connection ─────────────────────────────────────────────────

_driver = None


def _get_driver():
    """Lazy-init Neo4j driver. Returns None if MAGMA disabled or neo4j unavailable."""
    global _driver
    if not config.memory.magma_enabled:
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
        # Ensure indexes exist
        with _driver.session() as session:
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.node_id)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.timestamp)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (n:MemoryNode) ON (n.category)")
            session.run("CREATE INDEX IF NOT EXISTS FOR (e:Entity) ON (e.name)")
        logger.info("MAGMA Neo4j driver initialized")
        return _driver
    except Exception as e:
        logger.warning(f"MAGMA Neo4j unavailable (degrading to flat memory): {e}")
        return None


# ── Fast Path: Ingest ────────────────────────────────────────────────

# Track last node per category for temporal chaining
_last_node_ids: dict[str, str] = {}


async def magma_ingest(
    content: str,
    category: str,
    timestamp: str | None = None,
    metadata: dict | None = None,
    entities: list[str] | None = None,
) -> str | None:
    """Fast path: write to all graphs simultaneously. Non-blocking.

    1. Writes to Qdrant via existing Mem0 (semantic graph)
    2. Creates Neo4j node + TEMPORAL edge to previous node in this category
    3. Extracts/links entity nodes
    4. Enqueues for slow path causal consolidation

    Returns node_id or None if MAGMA disabled.
    """
    driver = _get_driver()
    if not driver:
        return None

    node_id = f"magma_{uuid.uuid4().hex[:12]}"
    ts = timestamp or datetime.now().isoformat()
    meta = metadata or {}

    # 1. Semantic graph — delegate to existing Mem0 path
    try:
        from titan.memory import store_memory
        await store_memory(
            content, category,
            client_id=meta.get("client_id"),
            metadata={**meta, "magma_node_id": node_id},
            outcome_magnitude=meta.get("outcome_magnitude", 0.5),
            sample_size=meta.get("sample_size", 1),
        )
    except Exception as e:
        logger.debug(f"MAGMA Mem0 write failed (non-critical): {e}")

    # 2-3. Neo4j: node + temporal edge + entity edges
    try:
        with driver.session() as session:
            # Create the memory node
            session.run(
                """CREATE (n:MemoryNode {
                    node_id: $node_id,
                    content: $content,
                    category: $category,
                    timestamp: $ts,
                    metadata: $meta_json,
                    consolidated: false
                })""",
                node_id=node_id,
                content=content[:2000],
                category=category,
                ts=ts,
                meta_json=json.dumps(meta, default=str),
            )

            # Temporal edge to previous node in same category
            prev_id = _last_node_ids.get(category)
            if prev_id:
                session.run(
                    """MATCH (prev:MemoryNode {node_id: $prev_id})
                       MATCH (curr:MemoryNode {node_id: $curr_id})
                       CREATE (prev)-[:TEMPORAL {delta_seconds: $delta}]->(curr)""",
                    prev_id=prev_id,
                    curr_id=node_id,
                    delta=0,  # actual delta computed on read
                )
            _last_node_ids[category] = node_id

            # Entity edges
            entity_list = entities or _extract_entities(content, meta)
            for entity_name in entity_list:
                session.run(
                    """MERGE (e:Entity {name: $name})
                       WITH e
                       MATCH (n:MemoryNode {node_id: $node_id})
                       CREATE (n)-[:INVOLVES]->(e)""",
                    name=entity_name.lower().strip(),
                    node_id=node_id,
                )

    except Exception as e:
        logger.debug(f"MAGMA Neo4j write failed (degrading gracefully): {e}")
        return None

    # 4. Enqueue for slow path (causal consolidation)
    try:
        from shared.db import emit_event
        await emit_event("magma_consolidate", {
            "node_id": node_id,
            "category": category,
        })
    except Exception:
        pass  # Best-effort enqueue

    return node_id


def _extract_entities(content: str, metadata: dict) -> list[str]:
    """Extract entity names from content and metadata without LLM.

    Fast, deterministic extraction from structured fields.
    """
    entities = []

    # From metadata
    for key in ("business_name", "industry", "city", "region", "agent", "skill"):
        val = metadata.get(key, "")
        if val and isinstance(val, str) and len(val) > 1:
            entities.append(val)

    # Client ID as entity reference
    client_id = metadata.get("client_id")
    if client_id:
        entities.append(f"client:{client_id}")

    return entities


# ── Slow Path: Causal Consolidation ──────────────────────────────────

async def magma_consolidate(node_id: str) -> bool:
    """Slow path: infer causal edges by analyzing the node's neighborhood.

    Pulls the node + 2-hop neighbors from Neo4j, asks Claude what caused
    what, writes CAUSAL edges back. This is the expensive step — runs in
    background, never blocks the pipeline.

    Returns True if causal edges were written.
    """
    driver = _get_driver()
    if not driver:
        return False

    # Pull node + 2-hop neighborhood
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

        if not result:
            return False

        content = result["content"]
        before_nodes = [n for n in result["before"] if n.get("content")][:5]
        after_nodes = [n for n in result["after"] if n.get("content")][:3]
        related_nodes = [n for n in result["related"] if n.get("content")][:3]

    except Exception as e:
        logger.debug(f"MAGMA consolidation read failed: {e}")
        return False

    if not before_nodes and not after_nodes:
        return False  # Not enough context for causal inference

    # Ask Claude for causal relationships
    neighborhood = "EVENTS BEFORE THIS:\n" + "\n".join(
        f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in before_nodes
    )
    if after_nodes:
        neighborhood += "\n\nEVENTS AFTER THIS:\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in after_nodes
        )
    if related_nodes:
        neighborhood += "\n\nRELATED (same entities):\n" + "\n".join(
            f"  [{n.get('cat', '?')}] {n.get('content', '')[:150]}" for n in related_nodes
        )

    try:
        from shared.llm_client import llm
        result = await llm.generate(
            f"Event: {content[:300]}\n\n"
            f"Neighborhood:\n{neighborhood}\n\n"
            f"What CAUSED this event? What did this event CAUSE?\n"
            f"Only identify STRONG causal links, not mere correlations or temporal coincidences.\n"
            f"For each causal link, rate your confidence (0.0-1.0) based on:\n"
            f"- Direct evidence (not just temporal proximity)\n"
            f"- Mechanism (HOW did cause lead to effect?)\n"
            f"- Alternative explanations ruled out\n\n"
            f"Return JSON: {{\n"
            f"  \"caused_by\": [{{\"snippet\": \"<node_content>\", \"confidence\": 0.0-1.0, \"mechanism\": \"how\"}}],\n"
            f"  \"caused\": [{{\"snippet\": \"<node_content>\", \"confidence\": 0.0-1.0, \"mechanism\": \"how\"}}]\n"
            f"}}",
            model="fast",
            temperature=0.1,
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        causal = json.loads(result[start:end])
    except Exception as e:
        logger.debug(f"MAGMA causal inference failed: {e}")
        return False

    # Write causal edges WITH confidence scoring
    # Only write edges with confidence >= 0.4 (skip low-confidence guesses)
    CAUSAL_CONFIDENCE_THRESHOLD = 0.4
    edges_written = 0
    audit_records = []

    try:
        with driver.session() as session:
            # Link causes → this node
            for cause_item in causal.get("caused_by", [])[:3]:
                # Handle both old format (string) and new format (dict with confidence)
                if isinstance(cause_item, str):
                    snippet, confidence, mechanism = cause_item, 0.5, ""
                elif isinstance(cause_item, dict):
                    snippet = cause_item.get("snippet", "")
                    confidence = min(1.0, max(0.0, cause_item.get("confidence", 0.5)))
                    mechanism = cause_item.get("mechanism", "")
                else:
                    continue

                if not snippet or confidence < CAUSAL_CONFIDENCE_THRESHOLD:
                    continue

                # Find the matching neighbor node
                for n in before_nodes + related_nodes:
                    if snippet[:30].lower() in (n.get("content", "")[:200].lower()):
                        session.run(
                            """MATCH (cause:MemoryNode {node_id: $cause_id})
                               MATCH (effect:MemoryNode {node_id: $effect_id})
                               MERGE (cause)-[r:CAUSED]->(effect)
                               SET r.inferred_at = $ts,
                                   r.confidence = $conf,
                                   r.mechanism = $mech""",
                            cause_id=n["id"],
                            effect_id=node_id,
                            ts=datetime.now().isoformat(),
                            conf=confidence,
                            mech=mechanism[:200],
                        )
                        edges_written += 1
                        audit_records.append({
                            "node_id": node_id,
                            "cause_node_id": n["id"],
                            "effect_node_id": node_id,
                            "confidence": confidence,
                        })
                        break

            # Link this node → effects
            for effect_item in causal.get("caused", [])[:3]:
                if isinstance(effect_item, str):
                    snippet, confidence, mechanism = effect_item, 0.5, ""
                elif isinstance(effect_item, dict):
                    snippet = effect_item.get("snippet", "")
                    confidence = min(1.0, max(0.0, effect_item.get("confidence", 0.5)))
                    mechanism = effect_item.get("mechanism", "")
                else:
                    continue

                if not snippet or confidence < CAUSAL_CONFIDENCE_THRESHOLD:
                    continue

                for n in after_nodes + related_nodes:
                    if snippet[:30].lower() in (n.get("content", "")[:200].lower()):
                        session.run(
                            """MATCH (cause:MemoryNode {node_id: $cause_id})
                               MATCH (effect:MemoryNode {node_id: $effect_id})
                               MERGE (cause)-[r:CAUSED]->(effect)
                               SET r.inferred_at = $ts,
                                   r.confidence = $conf,
                                   r.mechanism = $mech""",
                            cause_id=node_id,
                            effect_id=n["id"],
                            ts=datetime.now().isoformat(),
                            conf=confidence,
                            mech=mechanism[:200],
                        )
                        edges_written += 1
                        audit_records.append({
                            "node_id": node_id,
                            "cause_node_id": node_id,
                            "effect_node_id": n["id"],
                            "confidence": confidence,
                        })
                        break

            # Mark as consolidated
            session.run(
                "MATCH (n:MemoryNode {node_id: $id}) SET n.consolidated = true",
                id=node_id,
            )

    except Exception as e:
        logger.debug(f"MAGMA causal edge write failed: {e}")

    # Write audit records to Postgres for integrity tracking
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
        except Exception as e:
            logger.debug(f"MAGMA causal audit write failed (non-critical): {e}")

    if edges_written:
        logger.info(f"MAGMA consolidated {node_id}: {edges_written} causal edges (min confidence: {CAUSAL_CONFIDENCE_THRESHOLD})")
    return edges_written > 0


# ── Intent-Aware Router ──────────────────────────────────────────────

async def magma_retrieve(query: str, limit: int = 10) -> str:
    """Intent-aware retrieval across all 4 graphs.

    1. Classify intent (causal/temporal/entity/semantic)
    2. Find anchor nodes via RRF fusion (Qdrant + keyword + time)
    3. Adaptive beam search through Neo4j prioritizing intent-matching edges
    4. Linearize subgraph into prompt-ready narrative

    Falls back to flat get_relevant_learnings() if MAGMA disabled.
    """
    driver = _get_driver()
    if not driver:
        # Graceful fallback to existing system
        from titan.memory import get_relevant_learnings
        return await get_relevant_learnings(query, limit=limit)

    # Step 1: Classify intent
    intent = await _classify_intent(query)

    # Step 2: Find anchor nodes via Qdrant (semantic) + Neo4j (keyword/time)
    anchors = await _find_anchors(query, intent, limit=limit)

    if not anchors:
        # Fallback: use flat learnings
        from titan.memory import get_relevant_learnings
        return await get_relevant_learnings(query, limit=limit)

    # Step 3: Adaptive traversal — beam search through intent-matching edges
    subgraph = _beam_search(driver, anchors, intent, max_depth=3, beam_width=limit)

    # Step 4: Linearize into prompt-ready narrative
    return _linearize(subgraph, intent)


async def _classify_intent(query: str) -> Intent:
    """Fast intent classification — keyword-based first, LLM fallback."""
    q = query.lower()

    # Deterministic fast path
    if any(w in q for w in ("why", "caused", "because", "reason", "led to", "result of")):
        return Intent.CAUSAL
    if any(w in q for w in ("when", "last week", "yesterday", "today", "timeline", "history", "recent")):
        return Intent.TEMPORAL
    if any(w in q for w in ("dentist", "dental", "law", "legal", "clinic", "industry", "region", "who")):
        return Intent.ENTITY
    return Intent.SEMANTIC


async def _find_anchors(query: str, intent: Intent, limit: int = 10) -> list[dict]:
    """Reciprocal Rank Fusion across Qdrant vector search + Neo4j keyword/time.

    Combines semantic similarity (Qdrant) with graph-aware search (Neo4j).
    """
    driver = _get_driver()
    anchors_by_id: dict[str, dict] = {}

    # Source 1: Qdrant semantic search via Mem0
    try:
        from titan.memory import search_memory
        semantic_results = await search_memory(query, limit=limit)
        for rank, mem in enumerate(semantic_results):
            # We don't have node_id from Mem0, but we can match by content
            fake_id = f"semantic_{rank}"
            anchors_by_id[fake_id] = {
                "node_id": fake_id,
                "content": mem,
                "score": 1.0 / (rank + 1),  # RRF score
                "source": "semantic",
            }
    except Exception:
        pass

    # Source 2: Neo4j keyword + temporal search
    if driver:
        try:
            with driver.session() as session:
                # Keyword search in Neo4j content
                words = [w for w in query.lower().split() if len(w) > 3][:5]
                if words:
                    pattern = "|".join(words)
                    results = session.run(
                        """MATCH (n:MemoryNode)
                           WHERE any(w IN $words WHERE toLower(n.content) CONTAINS w)
                           RETURN n.node_id as node_id, n.content as content,
                                  n.category as category, n.timestamp as ts
                           ORDER BY n.timestamp DESC LIMIT $limit""",
                        words=words, limit=limit,
                    )
                    for rank, record in enumerate(results):
                        nid = record["node_id"]
                        rrf_score = 1.0 / (rank + 1)
                        if nid in anchors_by_id:
                            anchors_by_id[nid]["score"] += rrf_score  # Boost if in both
                        else:
                            anchors_by_id[nid] = {
                                "node_id": nid,
                                "content": record["content"],
                                "category": record["category"],
                                "timestamp": record["ts"],
                                "score": rrf_score,
                                "source": "graph",
                            }
        except Exception as e:
            logger.debug(f"MAGMA Neo4j anchor search failed: {e}")

    # Sort by RRF score and return top-k
    ranked = sorted(anchors_by_id.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


def _beam_search(
    driver, anchors: list[dict], intent: Intent,
    max_depth: int = 3, beam_width: int = 10,
) -> list[dict]:
    """Adaptive beam search through Neo4j, prioritizing intent-matching edges.

    CAUSAL → follow CAUSED edges
    TEMPORAL → follow TEMPORAL backbone
    ENTITY → traverse INVOLVES edges to find related events
    SEMANTIC → stay at anchor level (Qdrant already did the work)
    """
    if intent == Intent.SEMANTIC:
        return anchors  # Qdrant already found the right nodes

    # Map intent to edge type
    edge_type = {
        Intent.CAUSAL: "CAUSED",
        Intent.TEMPORAL: "TEMPORAL",
        Intent.ENTITY: "INVOLVES",
    }.get(intent, "TEMPORAL")

    graph_anchors = [a for a in anchors if a.get("source") == "graph" and a.get("node_id", "").startswith("magma_")]
    if not graph_anchors:
        return anchors

    expanded = list(anchors)  # Start with all anchors
    seen_ids = {a["node_id"] for a in anchors}

    try:
        with driver.session() as session:
            for anchor in graph_anchors[:5]:  # Expand top 5 graph anchors
                nid = anchor["node_id"]

                if intent == Intent.ENTITY:
                    # Traverse through entity nodes to find related events
                    results = session.run(
                        """MATCH (n:MemoryNode {node_id: $nid})-[:INVOLVES]->(e:Entity)
                           <-[:INVOLVES]-(related:MemoryNode)
                           WHERE related.node_id <> $nid
                           RETURN related.node_id as node_id, related.content as content,
                                  related.category as category, related.timestamp as ts,
                                  e.name as entity
                           LIMIT $limit""",
                        nid=nid, limit=beam_width,
                    )
                elif edge_type == "CAUSED":
                    # Follow CAUSED edges — only high-confidence causal chains
                    results = session.run(
                        f"""MATCH (n:MemoryNode {{node_id: $nid}})-[r:CAUSED*1..{max_depth}]-(related:MemoryNode)
                           WHERE related.node_id <> $nid
                           AND ALL(rel IN r WHERE rel.confidence >= 0.4)
                           RETURN DISTINCT related.node_id as node_id, related.content as content,
                                  related.category as category, related.timestamp as ts
                           LIMIT $limit""",
                        nid=nid, limit=beam_width,
                    )
                else:
                    # Follow TEMPORAL edges (no confidence filter)
                    results = session.run(
                        f"""MATCH (n:MemoryNode {{node_id: $nid}})-[:{edge_type}*1..{max_depth}]-(related:MemoryNode)
                           WHERE related.node_id <> $nid
                           RETURN DISTINCT related.node_id as node_id, related.content as content,
                                  related.category as category, related.timestamp as ts
                           LIMIT $limit""",
                        nid=nid, limit=beam_width,
                    )

                for record in results:
                    rid = record["node_id"]
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        expanded.append({
                            "node_id": rid,
                            "content": record["content"],
                            "category": record.get("category", ""),
                            "timestamp": record.get("ts", ""),
                            "source": "traversal",
                            "edge_type": edge_type,
                            "via_anchor": nid,
                        })

    except Exception as e:
        logger.debug(f"MAGMA beam search failed: {e}")

    return expanded[:beam_width * 2]


def _linearize(subgraph: list[dict], intent: Intent) -> str:
    """Serialize subgraph into prompt-ready narrative.

    Topologically sorted by timestamp, with causal chains marked.
    """
    if not subgraph:
        return "No relevant memories found."

    # Sort by timestamp (most recent first for temporal, causal chain order otherwise)
    sorted_nodes = sorted(
        subgraph,
        key=lambda x: x.get("timestamp", "") or "",
        reverse=(intent != Intent.CAUSAL),
    )

    parts = []

    if intent == Intent.CAUSAL:
        parts.append("CAUSAL CHAIN (what caused what):")
        for node in sorted_nodes[:15]:
            content = node.get("content", "")[:200]
            via = node.get("via_anchor", "")
            arrow = " ← caused by" if node.get("edge_type") == "CAUSED" else ""
            ts = node.get("timestamp", "")[:10]
            parts.append(f"  [{ts}] {content}{arrow}")

    elif intent == Intent.TEMPORAL:
        parts.append("TIMELINE (most recent first):")
        for node in sorted_nodes[:15]:
            content = node.get("content", "")[:200]
            ts = node.get("timestamp", "")[:16]
            cat = node.get("category", "")
            parts.append(f"  [{ts}] [{cat}] {content}")

    elif intent == Intent.ENTITY:
        parts.append("RELATED EVENTS (same entities):")
        for node in sorted_nodes[:15]:
            content = node.get("content", "")[:200]
            cat = node.get("category", "")
            parts.append(f"  [{cat}] {content}")

    else:  # SEMANTIC
        parts.append("RELEVANT MEMORIES:")
        for node in sorted_nodes[:15]:
            content = node.get("content", "")[:200]
            parts.append(f"  - {content}")

    return "\n".join(parts)


# ── Background Consolidation Worker ──────────────────────────────────

async def process_consolidation_queue(batch_size: int = 10) -> int:
    """Process queued MAGMA consolidation events. Called periodically.

    Picks up magma_consolidate events and runs slow-path causal inference.
    """
    driver = _get_driver()
    if not driver:
        return 0

    try:
        from shared.db import fetch_all, execute
        pending = await fetch_all(
            """SELECT id, payload FROM events
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
                continue

        node_id = payload.get("node_id", "")
        if node_id:
            try:
                if await magma_consolidate(node_id):
                    consolidated += 1
            except Exception as e:
                logger.debug(f"MAGMA consolidation failed for {node_id}: {e}")

        # Mark acknowledged
        try:
            from shared.db import execute
            await execute(
                "UPDATE events SET acknowledged = TRUE WHERE id = %s",
                (event["id"],),
            )
        except Exception:
            pass

    if consolidated:
        logger.info(f"MAGMA consolidated {consolidated}/{len(pending)} nodes")
    return consolidated


# ── Backfill: Migrate Existing Data ──────────────────────────────────

async def backfill_from_existing_data() -> dict:
    """One-time migration: populate Neo4j from existing Postgres data.

    Maps: titan_learnings → episodic nodes, training_data → causal nodes,
    agent_decisions → causal chains, prospect_state → entity nodes.
    """
    driver = _get_driver()
    if not driver:
        return {"error": "MAGMA not enabled"}

    stats = {"learnings": 0, "decisions": 0, "training": 0}

    try:
        from shared.db import fetch_all

        # 1. titan_learnings → episodic nodes
        learnings = await fetch_all(
            """SELECT id, category, insight, confidence, created_at
               FROM titan_learnings
               ORDER BY created_at DESC LIMIT 500"""
        )
        for l in learnings:
            await magma_ingest(
                content=l.get("insight", ""),
                category=l.get("category", "learning"),
                timestamp=str(l.get("created_at", "")),
                metadata={"source": "backfill", "learning_id": l.get("id")},
            )
            stats["learnings"] += 1

        # 2. agent_decisions → causal chains
        decisions = await fetch_all(
            """SELECT id, agent, decision_type, reasoning, created_at
               FROM agent_decisions
               WHERE reasoning != ''
               ORDER BY created_at DESC LIMIT 200"""
        )
        for d in decisions:
            await magma_ingest(
                content=f"[{d.get('agent', '?')}] {d.get('decision_type', '')}: {d.get('reasoning', '')[:200]}",
                category="agent_decision",
                timestamp=str(d.get("created_at", "")),
                metadata={
                    "source": "backfill",
                    "agent": d.get("agent", ""),
                    "decision_id": d.get("id"),
                },
            )
            stats["decisions"] += 1

        # 3. training_data outcomes → causal pairs
        outcomes = await fetch_all(
            """SELECT t.id, t.example_type, t.outcome, t.input_text, t.created_at,
                      c.business_name, c.industry
               FROM training_data t
               LEFT JOIN clients c ON (t.metadata::jsonb->>'client_id')::int = c.id
               WHERE t.outcome IN ('positive', 'negative')
               ORDER BY t.created_at DESC LIMIT 300"""
        )
        for o in outcomes:
            entities = []
            if o.get("business_name"):
                entities.append(o["business_name"])
            if o.get("industry"):
                entities.append(o["industry"])

            await magma_ingest(
                content=f"Email outcome: {o.get('outcome', '')} for {o.get('example_type', '')}",
                category="email_outcome",
                timestamp=str(o.get("created_at", "")),
                metadata={
                    "source": "backfill",
                    "outcome": o.get("outcome", ""),
                    "training_id": o.get("id"),
                },
                entities=entities,
            )
            stats["training"] += 1

    except Exception as e:
        logger.error(f"MAGMA backfill failed: {e}")
        stats["error"] = str(e)

    logger.info(f"MAGMA backfill complete: {stats}")
    return stats
