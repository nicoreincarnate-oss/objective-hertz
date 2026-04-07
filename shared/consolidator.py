"""Nightly episodic→semantic consolidator (Phase 42 Stage C).

Walks the recent rows in the `events` table, embeds them via Ollama, clusters
them by cosine similarity, then asks an LLM to summarize each significant
cluster.  Each summary becomes a high-confidence MAGMA semantic memory of type
``lesson_learned`` written by the synthetic ``consolidator`` daemon.

Designed to be invoked by Perseus at 03:00 daily, but the Perseus integration
lives in a separate sequential step — this module only knows how to do the
work when called.

Public API
----------
``consolidate_recent_events(hours_lookback, min_cluster_size, dry_run)``

The function is fully async and returns a stats dict with the count of events
examined, clusters formed, memories created and any non-fatal errors that
occurred while talking to the LLM, embedding service or database.
"""

from __future__ import annotations

import json
import logging
import math
import os
import uuid
from typing import Any

logger = logging.getLogger("perseus.consolidator")

# Tunables exposed as module constants so tests can monkeypatch if desired.
SIMILARITY_THRESHOLD: float = 0.6
DEFAULT_LOOKBACK_HOURS: int = 24
DEFAULT_MIN_CLUSTER_SIZE: int = 5
SUMMARY_MAX_TOKENS: int = 300
OLLAMA_HOST: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_SUMMARY_MODEL: str = os.environ.get(
    "CONSOLIDATOR_OLLAMA_MODEL", "qwen2.5:3b"
)


# ───────────────────────── helpers ──────────────────────────


def _cosine(a: list[float], b: list[float]) -> float:
    """Return the cosine similarity of two equal-length vectors.

    Returns ``0.0`` for length mismatch or zero-norm vectors so the caller can
    treat it as "not similar" without raising.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _event_text(event: dict[str, Any]) -> str:
    """Build the canonical text used for embedding + LLM input."""
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {"raw": payload}
    summary = ""
    if isinstance(payload, dict):
        summary = str(
            payload.get("summary")
            or payload.get("message")
            or payload.get("description")
            or ""
        )
    return f"{event_type}: {summary}".strip()


async def _embed_text(text: str) -> list[float] | None:
    """Embed ``text`` via the Ollama embeddings endpoint.

    Returns ``None`` on any failure so the caller can simply skip the event.
    """
    try:
        import httpx  # local import keeps module import cheap for tests
    except ImportError:  # pragma: no cover - httpx is a hard dep in prod
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embedding")
            if isinstance(vec, list) and vec:
                return [float(x) for x in vec]
            return None
    except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        logger.debug("embed_text failed: %s", exc)
        return None


async def _llm_summarize(prompt: str) -> str | None:
    """Summarize ``prompt`` using the best available LLM.

    Tries the shared LLM client first (which already routes through Claude →
    Ollama with budget enforcement), then a direct Anthropic call, then a raw
    Ollama generate call.  Returns ``None`` if every option fails.
    """
    # 1) Preferred: shared LLM client (handles Claude/Ollama routing + budgets)
    try:
        from shared.llm_client import llm  # type: ignore[attr-defined]

        if llm is not None:
            result = await llm.generate(
                prompt,
                model="auto",
                max_tokens=SUMMARY_MAX_TOKENS,
                temperature=0.3,
                pipeline_stage="consolidator",
            )
            if isinstance(result, str) and result.strip():
                return result.strip()
    except (ImportError, AttributeError, RuntimeError, ValueError, OSError) as exc:
        logger.debug("shared.llm_client unavailable: %s", exc)

    # 2) Anthropic API directly (only if a key is configured)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": SUMMARY_MAX_TOKENS,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp.raise_for_status()
                blocks = resp.json().get("content", [])
                if blocks and isinstance(blocks, list):
                    text = blocks[0].get("text", "")
                    if text.strip():
                        return text.strip()
        except (ImportError, OSError, ValueError, KeyError) as exc:
            logger.debug("Anthropic direct call failed: %s", exc)

    # 3) Last resort: local Ollama generate
    try:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": OLLAMA_SUMMARY_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": SUMMARY_MAX_TOKENS},
                },
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            if text.strip():
                return text.strip()
    except (ImportError, OSError, ValueError, KeyError) as exc:
        logger.warning("Ollama summarize fallback failed: %s", exc)

    return None


def _greedy_cluster(
    embeddings: list[list[float]],
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[list[int]]:
    """Greedy clustering by cosine similarity.

    Repeatedly picks the unassigned event with the highest total similarity to
    the remaining pool, then absorbs every other event whose similarity to it
    exceeds ``threshold``.  Returns a list of clusters where each cluster is a
    list of indices into ``embeddings``.
    """
    n = len(embeddings)
    if n == 0:
        return []

    # Pre-compute the similarity matrix once.
    sim: list[list[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        sim[i][i] = 1.0
        for j in range(i + 1, n):
            s = _cosine(embeddings[i], embeddings[j])
            sim[i][j] = s
            sim[j][i] = s

    unassigned: set[int] = set(range(n))
    clusters: list[list[int]] = []
    while unassigned:
        # Pick the seed: the unassigned index with the largest sum of
        # similarities to other unassigned indices.
        seed = max(
            unassigned,
            key=lambda i: sum(sim[i][j] for j in unassigned if j != i),
        )
        cluster = [seed]
        for j in list(unassigned):
            if j == seed:
                continue
            if sim[seed][j] >= threshold:
                cluster.append(j)
        for idx in cluster:
            unassigned.discard(idx)
        clusters.append(cluster)
    return clusters


def _build_summary_prompt(events: list[dict[str, Any]]) -> str:
    lines = [
        f"Summarize what happened across these {len(events)} related events "
        "in 2-3 concise sentences. Focus on the pattern, outcome, and any "
        "lesson worth remembering long-term.",
        "",
    ]
    for ev in events:
        lines.append(f"- {_event_text(ev)}")
    lines.append("")
    lines.append("Summary:")
    return "\n".join(lines)


# ───────────────────────── persistence ──────────────────────────


async def _persist_memory(
    *,
    memory_id: str,
    summary: str,
    embedding: list[float] | None,
    cluster_event_ids: list[int],
    cluster_size: int,
) -> None:
    """Insert the consolidated memory + provenance + DERIVED_FROM edges.

    Each INSERT is wrapped individually so a partial failure (e.g. an edge
    table that doesn't exist yet) can't take the whole consolidation down.
    """
    from shared.db import execute  # local import → easy to patch in tests

    # 1) MAGMA node row.
    try:
        await execute(
            """INSERT INTO magma_nodes
                   (id, content, embedding, confidence, source_daemon, type, domain, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (id) DO NOTHING""",
            (
                memory_id,
                summary,
                json.dumps(embedding) if embedding is not None else None,
                0.85,
                "consolidator",
                "lesson_learned",
                "consolidated",
            ),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort persistence
        logger.warning("magma_nodes insert failed for %s: %s", memory_id, exc)

    # 2) Provenance row pointing back at the source events.
    try:
        await execute(
            """INSERT INTO memory_provenance
                   (magma_node_id, source_daemon, source_table, source_record_id, visibility)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (magma_node_id) DO NOTHING""",
            (
                memory_id,
                "consolidator",
                "consolidator",
                "|".join(str(eid) for eid in cluster_event_ids),
                "public",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_provenance insert failed for %s: %s", memory_id, exc)

    # 3) DERIVED_FROM edges, one per source event.
    for eid in cluster_event_ids:
        try:
            await execute(
                """INSERT INTO magma_edges
                       (source_id, target_id, edge_type, weight, created_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                (memory_id, f"event:{eid}", "DERIVED_FROM", 1.0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "magma_edges insert failed for %s→event:%s: %s",
                memory_id,
                eid,
                exc,
            )

    logger.info(
        "consolidator: wrote memory %s from cluster of %d events",
        memory_id,
        cluster_size,
    )


# ───────────────────────── public API ──────────────────────────


async def consolidate_recent_events(
    hours_lookback: int = DEFAULT_LOOKBACK_HOURS,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Cluster recent events by topic and write summaries as MAGMA memories.

    Parameters
    ----------
    hours_lookback:
        How far back into the ``events`` table to look (default: 24 hours).
    min_cluster_size:
        Minimum number of events required for a cluster to be summarised.
        Smaller clusters are discarded as noise.
    dry_run:
        If ``True`` everything except the database writes happens — useful for
        operators who want to see what would be created before authorising the
        run.

    Returns
    -------
    dict
        Stats with keys ``events_examined``, ``clusters_found``,
        ``memories_created`` and ``errors``.
    """
    stats: dict[str, Any] = {
        "events_examined": 0,
        "clusters_found": 0,
        "memories_created": 0,
        "errors": [],
    }

    # 1) Pull recent events.
    try:
        from shared.db import fetch_all
    except ImportError as exc:
        stats["errors"].append(f"db import failed: {exc}")
        return stats

    try:
        rows = await fetch_all(
            """SELECT id, event_type, payload, created_at
                 FROM events
                WHERE created_at >= NOW() - (%s || ' hours')::interval
             ORDER BY created_at ASC""",
            (str(int(hours_lookback)),),
        )
    except Exception as exc:  # noqa: BLE001
        stats["errors"].append(f"events query failed: {exc}")
        return stats

    events: list[dict[str, Any]] = list(rows or [])
    stats["events_examined"] = len(events)
    if not events:
        return stats

    # 2) Embed each event.  Events that fail to embed are dropped (with an
    # error logged) so a single bad row can't sink the whole run.
    embedded: list[tuple[dict[str, Any], list[float]]] = []
    for ev in events:
        text = _event_text(ev)
        if not text:
            continue
        vec = await _embed_text(text)
        if vec is None:
            stats["errors"].append(
                f"embed failed for event id={ev.get('id')}"
            )
            continue
        embedded.append((ev, vec))

    if len(embedded) < min_cluster_size:
        return stats

    # 3) Cluster.
    embeddings_only = [vec for _, vec in embedded]
    raw_clusters = _greedy_cluster(embeddings_only, SIMILARITY_THRESHOLD)
    significant = [c for c in raw_clusters if len(c) >= min_cluster_size]
    stats["clusters_found"] = len(significant)
    if not significant:
        return stats

    # 4) Summarize each significant cluster and persist.
    for cluster_indices in significant:
        cluster_events = [embedded[i][0] for i in cluster_indices]
        cluster_event_ids = [int(ev["id"]) for ev in cluster_events]
        prompt = _build_summary_prompt(cluster_events)

        summary = await _llm_summarize(prompt)
        if not summary:
            stats["errors"].append(
                f"llm summarize failed for cluster of {len(cluster_events)} events"
            )
            continue

        summary_embedding = await _embed_text(summary)
        memory_id = str(uuid.uuid4())

        if dry_run:
            stats["memories_created"] += 1
            logger.info(
                "consolidator dry-run: would create memory %s from %d events",
                memory_id,
                len(cluster_events),
            )
            continue

        try:
            await _persist_memory(
                memory_id=memory_id,
                summary=summary,
                embedding=summary_embedding,
                cluster_event_ids=cluster_event_ids,
                cluster_size=len(cluster_events),
            )
            stats["memories_created"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(
                f"persist failed for memory {memory_id}: {exc}"
            )

    return stats
