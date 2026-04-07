"""Operator interaction memory hook — pinned, no-decay MAGMA memories.

Records direct human (operator) interactions with the system as MAGMA
memories. Operator input is the most authoritative signal in Hertz, so
every memory written by this module is:

    * source_daemon = "operator"
    * confidence    = 1.0
    * pinned        = True   (excluded from decay/eviction)
    * visibility    = "tier1"

The module exposes three coroutines:

    * record_operator_chat       — chat exchange (user msg + agent reply,
                                   linked by a CAUSED_BY edge)
    * record_operator_decision   — explicit approve/reject/override on a
                                   target memory (SUPPORTS / CONTRADICTS
                                   edge)
    * record_operator_pin        — pin an existing memory and attach a
                                   small annotation node via MENTIONS

All write paths degrade gracefully when Neo4j or Postgres are
unavailable, mirroring the behaviour of ``shared.magma``.

Phase 42 / Agent A7 — Wave 1.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

OPERATOR_DAEMON = "operator"
OPERATOR_VISIBILITY = "tier1"
OPERATOR_CONFIDENCE = 1.0
OPERATOR_CATEGORY = "operator_interaction"

_APPROVE_DECISIONS: frozenset[str] = frozenset({"approve", "approved", "accept", "accepted", "confirm", "confirmed"})
_REJECT_DECISIONS: frozenset[str] = frozenset({"reject", "rejected", "deny", "denied", "veto", "block"})


# ── Internal helpers ─────────────────────────────────────────────────


def _new_node_id() -> str:
    """Generate a fresh MAGMA-style node id."""
    return f"magma_op_{uuid.uuid4().hex[:12]}"


async def _safe_embedding(text: str) -> list[float] | None:
    """Compute an embedding via the shared MAGMA helper, swallowing errors.

    The MAGMA helper already degrades gracefully when Ollama is offline,
    but we wrap it again so that an unrelated import-time failure (e.g.
    httpx missing in a stripped test image) cannot prevent operator
    memories from being recorded.
    """
    try:
        from shared.magma import _get_embedding  # local import — avoid cycle on module load
        return await _get_embedding(text)
    except Exception as exc:  # noqa: BLE001 — best-effort, must not block writes
        logger.debug("operator hook embedding fallback (non-critical): %s", exc)
        return None


def _embedding_hash_safe(vec: list[float] | None) -> str:
    if not vec:
        return ""
    try:
        from shared.magma import _embedding_hash
        return _embedding_hash(vec)
    except Exception as exc:  # noqa: BLE001
        logger.debug("operator hook embedding hash failed: %s", exc)
        return ""


def _get_driver() -> Any | None:
    """Return the shared Neo4j driver, or None if unavailable."""
    try:
        from shared.magma import _get_driver as _shared_driver
        return _shared_driver()
    except Exception as exc:  # noqa: BLE001
        logger.debug("operator hook driver unavailable: %s", exc)
        return None


async def _record_provenance(node_id: str, source_record_id: str) -> None:
    """Insert a provenance row for an operator-authored node."""
    try:
        from shared.db import execute
        await execute(
            """INSERT INTO memory_provenance
                   (magma_node_id, source_daemon, source_table, source_record_id, visibility)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (magma_node_id) DO NOTHING""",
            (
                node_id,
                OPERATOR_DAEMON,
                "operator_chat",
                source_record_id,
                OPERATOR_VISIBILITY,
            ),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("operator hook provenance insert failed (non-critical): %s", exc)


def _create_node(session: Any, node_id: str, content: str, metadata: dict[str, Any], emb_hash: str) -> None:
    """Create an operator MemoryNode in Neo4j (pinned, tier1, conf=1.0)."""
    session.run(
        """CREATE (n:MemoryNode {
            node_id: $node_id,
            content: $content,
            category: $category,
            timestamp: $ts,
            metadata: $meta_json,
            embedding_hash: $emb_hash,
            source_daemon: $source_daemon,
            visibility: $visibility,
            confidence: $confidence,
            pinned: true,
            consolidated: false
        })""",
        node_id=node_id,
        content=content[:4000],
        category=OPERATOR_CATEGORY,
        ts=datetime.now().isoformat(),
        meta_json=json.dumps(metadata, default=str),
        emb_hash=emb_hash,
        source_daemon=OPERATOR_DAEMON,
        visibility=OPERATOR_VISIBILITY,
        confidence=OPERATOR_CONFIDENCE,
    )


def _create_edge(session: Any, src_id: str, dst_id: str, edge_type: str, properties: dict[str, Any] | None = None) -> None:
    """Create a typed edge between two MemoryNodes.

    ``edge_type`` is interpolated into the Cypher query because Neo4j
    does not support parameterised relationship types. The caller is
    responsible for restricting it to a known whitelist.
    """
    props = properties or {}
    session.run(
        f"""MATCH (a:MemoryNode {{node_id: $src}})
            MATCH (b:MemoryNode {{node_id: $dst}})
            CREATE (a)-[r:{edge_type}]->(b)
            SET r += $props""",
        src=src_id,
        dst=dst_id,
        props=props,
    )


# ── Public API ───────────────────────────────────────────────────────


async def record_operator_chat(
    message: str,
    response: str,
    intent: str | None = None,
) -> str:
    """Record an operator chat exchange as two linked MAGMA nodes.

    Creates a node for the operator's *message* and another for the
    agent *response*, then connects them with a ``CAUSED_BY`` edge
    (response CAUSED_BY message). Both nodes are pinned tier1 memories
    with confidence 1.0.

    Returns the node id of the *message* node (the originating operator
    utterance), or an empty string if Neo4j is unavailable.
    """
    if not message:
        raise ValueError("operator chat message must not be empty")

    driver = _get_driver()
    if driver is None:
        logger.debug("operator hook: chat skipped — Neo4j unavailable")
        return ""

    msg_id = _new_node_id()
    resp_id = _new_node_id()

    msg_emb = await _safe_embedding(message)
    resp_emb = await _safe_embedding(response) if response else None
    msg_hash = _embedding_hash_safe(msg_emb)
    resp_hash = _embedding_hash_safe(resp_emb)

    metadata: dict[str, Any] = {
        "kind": "operator_chat",
        "intent": intent or "",
    }

    try:
        with driver.session() as session:
            _create_node(session, msg_id, message, {**metadata, "role": "operator"}, msg_hash)
            _create_node(session, resp_id, response or "", {**metadata, "role": "agent"}, resp_hash)
            # response CAUSED_BY message  →  agent reply caused by operator input
            _create_edge(session, resp_id, msg_id, "CAUSED_BY", {"intent": intent or ""})
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        logger.warning("operator hook chat write failed: %s", exc)
        return ""

    await _record_provenance(msg_id, f"chat:{msg_id}")
    await _record_provenance(resp_id, f"chat:{resp_id}")

    return msg_id


async def record_operator_decision(
    decision_type: str,
    target_id: str,
    rationale: str | None = None,
) -> str:
    """Record an explicit operator decision against an existing memory.

    ``decision_type`` is normalised case-insensitively. Approve-style
    decisions emit a ``SUPPORTS`` edge (decision → target); reject-style
    decisions emit ``CONTRADICTS``. Anything else is recorded as an
    ``OVERRIDES`` edge so the system retains the audit trail without
    silently dropping unknown decision types.
    """
    if not decision_type:
        raise ValueError("decision_type must not be empty")
    if not target_id:
        raise ValueError("target_id must not be empty")

    norm = decision_type.strip().lower()
    if norm in _APPROVE_DECISIONS:
        edge_type = "SUPPORTS"
    elif norm in _REJECT_DECISIONS:
        edge_type = "CONTRADICTS"
    else:
        edge_type = "OVERRIDES"

    driver = _get_driver()
    if driver is None:
        logger.debug("operator hook: decision skipped — Neo4j unavailable")
        return ""

    node_id = _new_node_id()
    content = f"Operator decision: {decision_type} → {target_id}"
    if rationale:
        content += f"\nRationale: {rationale}"

    embedding = await _safe_embedding(content)
    emb_hash = _embedding_hash_safe(embedding)

    metadata: dict[str, Any] = {
        "kind": "operator_decision",
        "decision_type": norm,
        "target_id": target_id,
        "rationale": rationale or "",
    }

    try:
        with driver.session() as session:
            _create_node(session, node_id, content, metadata, emb_hash)
            _create_edge(
                session,
                node_id,
                target_id,
                edge_type,
                {"decision_type": norm, "rationale": rationale or ""},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("operator hook decision write failed: %s", exc)
        return ""

    await _record_provenance(node_id, f"decision:{node_id}")
    return node_id


async def record_operator_pin(
    node_id: str,
    note: str | None = None,
) -> str:
    """Pin an existing MAGMA memory and attach an operator annotation.

    Sets ``pinned=true`` on the target node so the consolidation/eviction
    pipeline never decays or removes it, then creates a small annotation
    node connected via a ``MENTIONS`` edge so the audit trail records
    *who* pinned it (and optionally why).

    Returns the annotation node id, or an empty string if Neo4j is
    unavailable.
    """
    if not node_id:
        raise ValueError("node_id must not be empty")

    driver = _get_driver()
    if driver is None:
        logger.debug("operator hook: pin skipped — Neo4j unavailable")
        return ""

    annotation_id = _new_node_id()
    annotation_content = "Pinned by operator"
    if note:
        annotation_content += f": {note}"

    embedding = await _safe_embedding(annotation_content)
    emb_hash = _embedding_hash_safe(embedding)

    metadata: dict[str, Any] = {
        "kind": "operator_pin",
        "target_id": node_id,
        "note": note or "",
    }

    try:
        with driver.session() as session:
            session.run(
                """MATCH (n:MemoryNode {node_id: $node_id})
                   SET n.pinned = true,
                       n.pinned_by = $daemon,
                       n.pinned_at = $ts""",
                node_id=node_id,
                daemon=OPERATOR_DAEMON,
                ts=datetime.now().isoformat(),
            )
            _create_node(session, annotation_id, annotation_content, metadata, emb_hash)
            _create_edge(
                session,
                annotation_id,
                node_id,
                "MENTIONS",
                {"note": note or ""},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("operator hook pin write failed: %s", exc)
        return ""

    await _record_provenance(annotation_id, f"pin:{node_id}")
    return annotation_id


__all__ = [
    "OPERATOR_CATEGORY",
    "OPERATOR_CONFIDENCE",
    "OPERATOR_DAEMON",
    "OPERATOR_VISIBILITY",
    "record_operator_chat",
    "record_operator_decision",
    "record_operator_pin",
]
