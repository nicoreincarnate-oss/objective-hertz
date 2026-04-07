"""Procedural memory extractor for completed daemon tasks.

When a daemon successfully completes a task, this module extracts the workflow
(tool-call sequence) and writes it as a procedural memory in the MAGMA node
table. Over time this builds a "how to do X" library that other daemons can
retrieve and replay.

Phase 42 — Agent A4. Wave 1.

Notes
-----
- Procedures are deduplicated by a deterministic *signature* derived from the
  tool sequence. Re-encountering an existing procedure increments its access
  counters instead of creating a duplicate node.
- Failed and trivial (single-tool) tasks are skipped — there is nothing to
  generalize from them.
- Embeddings + LLM summaries are best-effort; if the LLM client is unavailable
  we fall back to ``task_summary`` and a zero vector.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from shared import db

logger = logging.getLogger(__name__)

# Module-level constants -------------------------------------------------------

#: Minimum number of tool calls required for a task to qualify as a procedure.
MIN_TOOL_CALLS_FOR_PROCEDURE = 2

#: Confidence assigned to freshly extracted procedures.
DEFAULT_PROCEDURE_CONFIDENCE = 0.9

#: Embedding dimension fallback when no LLM client is reachable.
EMBEDDING_DIM = 1536


def compute_procedure_signature(tool_calls: list[dict[str, Any]]) -> str:
    """Return a deterministic signature for a tool-call sequence.

    The signature is the pipe-joined sequence of tool names, hashed with
    SHA-256 and prefixed for readability. Two tasks that invoke the same tools
    in the same order produce the same signature regardless of arguments.

    Parameters
    ----------
    tool_calls:
        Ordered list of tool-call dicts. Each entry must have a ``"tool"``
        key. Missing keys are tolerated and rendered as ``"<unknown>"``.

    Returns
    -------
    str
        A signature string of the form ``"proc:<sha256-hex>"``.
    """
    sequence = "|".join(str(tc.get("tool", "<unknown>")) for tc in tool_calls)
    digest = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    return f"proc:{digest}"


async def _generate_summary(task_summary: str, tool_calls: list[dict[str, Any]]) -> str:
    """Best-effort LLM summary; falls back to ``task_summary``.

    Kept tiny so it can be monkeypatched easily in tests.
    """
    try:
        from shared import llm_client  # local import to avoid hard dep at import time

        prompt = (
            "In one short sentence, describe what this procedure accomplishes. "
            f"Task: {task_summary}. Tools used: "
            f"{', '.join(tc.get('tool', '?') for tc in tool_calls)}."
        )
        result = await llm_client.complete(prompt, max_tokens=80)  # type: ignore[attr-defined]
        if isinstance(result, str) and result.strip():
            return result.strip()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("procedural_extractor: LLM summary unavailable (%s)", exc)
    return task_summary


async def _generate_embedding(text: str) -> list[float]:
    """Best-effort embedding; returns a zero vector on failure."""
    try:
        from shared import llm_client

        vec = await llm_client.embed(text)  # type: ignore[attr-defined]
        if isinstance(vec, list) and vec:
            return vec
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("procedural_extractor: embedding unavailable (%s)", exc)
    return [0.0] * EMBEDDING_DIM


async def extract_procedure_from_task(
    daemon: str,
    task_id: str,
    task_summary: str,
    tool_calls: list[dict[str, Any]],
    success: bool,
    duration_seconds: float,
) -> str | None:
    """Extract a procedural memory from a completed task.

    Parameters
    ----------
    daemon:
        Name of the daemon that ran the task (e.g. ``"titan"``).
    task_id:
        Identifier of the source task — recorded in provenance.
    task_summary:
        One-line description of what the task accomplished.
    tool_calls:
        Ordered tool-call records. Each item should be a dict with at least
        ``"tool"`` and may include ``"args"`` and ``"result_summary"``.
    success:
        Whether the task completed successfully.
    duration_seconds:
        End-to-end wallclock duration of the task in seconds.

    Returns
    -------
    str | None
        The MAGMA node ID of the procedure (existing or newly inserted), or
        ``None`` when the task was skipped (failure or trivial).
    """
    if not success:
        logger.debug("procedural_extractor: skipping failed task %s", task_id)
        return None

    if len(tool_calls) < MIN_TOOL_CALLS_FOR_PROCEDURE:
        logger.debug(
            "procedural_extractor: skipping trivial task %s (%d tool calls)",
            task_id,
            len(tool_calls),
        )
        return None

    signature = compute_procedure_signature(tool_calls)

    # Look for an existing procedure with the same signature for this daemon.
    existing = await db.fetch_one(
        """
        SELECT id
          FROM magma_nodes
         WHERE type = 'procedure'
           AND domain = %s
           AND payload->>'signature' = %s
         LIMIT 1
        """,
        (daemon, signature),
    )

    if existing is not None:
        existing_id = existing["id"]
        await db.execute(
            """
            UPDATE magma_nodes
               SET access_count = COALESCE(access_count, 0) + 1,
                   last_used_at = NOW()
             WHERE id = %s
            """,
            (existing_id,),
        )
        logger.info(
            "procedural_extractor: reused procedure %s for daemon=%s",
            existing_id,
            daemon,
        )
        return str(existing_id)

    # Novel procedure — synthesize summary + embedding and insert it.
    summary = await _generate_summary(task_summary, tool_calls)
    embedding = await _generate_embedding(summary)

    payload: dict[str, Any] = {
        "signature": signature,
        "tool_calls": tool_calls,
        "duration_seconds": duration_seconds,
        "task_summary": task_summary,
        "summary": summary,
    }

    inserted = await db.fetch_one(
        """
        INSERT INTO magma_nodes (type, domain, confidence, payload, embedding, created_at, last_used_at, access_count)
        VALUES ('procedure', %s, %s, %s, %s, NOW(), NOW(), 1)
        RETURNING id
        """,
        (daemon, DEFAULT_PROCEDURE_CONFIDENCE, json.dumps(payload), embedding),
    )

    if inserted is None:  # pragma: no cover - defensive
        logger.warning("procedural_extractor: INSERT returned no row for task %s", task_id)
        return None

    new_id = str(inserted["id"])

    # Provenance row — best effort, do not fail the extraction if absent.
    try:
        await db.execute(
            """
            INSERT INTO magma_provenance (node_id, source_kind, source_ref, created_at)
            VALUES (%s, 'task', %s, NOW())
            """,
            (new_id, task_id),
        )
    except Exception as exc:
        logger.debug("procedural_extractor: provenance insert failed (%s)", exc)

    # OWNED_BY edge to the daemon pseudo-node. Use ensure_daemon_node from
    # the magma_writer companion module if available; otherwise fall back to
    # the deterministic ``daemon:<name>`` ID.
    daemon_node_id: str = f"daemon:{daemon}"
    try:
        from shared import magma_writer  # type: ignore[attr-defined]

        ensure = getattr(magma_writer, "ensure_daemon_node", None)
        if ensure is not None:
            daemon_node_id = await ensure(daemon)
    except Exception as exc:
        logger.debug("procedural_extractor: magma_writer not available (%s)", exc)

    try:
        await db.execute(
            """
            INSERT INTO magma_edges (src_id, dst_id, type, created_at)
            VALUES (%s, %s, 'OWNED_BY', NOW())
            """,
            (new_id, daemon_node_id),
        )
    except Exception as exc:
        logger.debug("procedural_extractor: OWNED_BY edge insert failed (%s)", exc)

    logger.info(
        "procedural_extractor: stored new procedure %s for daemon=%s (signature=%s)",
        new_id,
        daemon,
        signature,
    )
    return new_id
