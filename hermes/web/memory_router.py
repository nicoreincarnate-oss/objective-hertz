"""Memory Explorer API — search, graph, and timeline endpoints.

Phase 29: Unified Memory Bus.  These endpoints power the Memory Explorer
page in the War Room dashboard.  All queries route through MAGMA with
3-tier access control (operator sees Tier 1 + Tier 2).

Feature flag: MEMORY_EXPLORER_ENABLED (default true).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("hermes.web.memory")

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _explorer_enabled() -> bool:
    return os.environ.get("MEMORY_EXPLORER_ENABLED", "true").lower() in ("true", "1", "yes")


@router.get("/search")
async def memory_search(
    q: str = Query("", description="Search query"),
    source: str | None = Query(None, description="Filter by daemon name"),
    memory_type: str | None = Query(None, description="Filter by memory type"),
    from_ts: float | None = Query(None, description="Time range start (epoch)"),
    to_ts: float | None = Query(None, description="Time range end (epoch)"),
    limit: int = Query(50, ge=1, le=500),
) -> JSONResponse:
    """Search across all memory stores via MAGMA."""
    if not _explorer_enabled():
        return JSONResponse({"error": "Memory Explorer is disabled"}, status_code=503)

    try:
        # Search via memory_provenance table (Postgres) for basic search
        from shared.db import fetch_all
        from shared.magma import filter_by_access

        params: list = []
        where_clauses = ["1=1"]

        if source:
            where_clauses.append("p.source_daemon = %s")
            params.append(source)

        if memory_type:
            where_clauses.append("p.source_table = %s")
            params.append(memory_type)

        if from_ts:
            where_clauses.append("p.ingested_at >= to_timestamp(%s)")
            params.append(from_ts)

        if to_ts:
            where_clauses.append("p.ingested_at <= to_timestamp(%s)")
            params.append(to_ts)

        where = " AND ".join(where_clauses)
        rows = await fetch_all(
            f"SELECT p.magma_node_id, p.source_daemon, p.source_table, "
            f"  p.source_record_id, p.visibility, p.ingested_at "
            f"FROM memory_provenance p "
            f"WHERE {where} "
            f"ORDER BY p.ingested_at DESC LIMIT %s",
            (*params, limit),
        )

        results = []
        for row in (rows or []):
            results.append({
                "id": row.get("magma_node_id", ""),
                "source_daemon": row.get("source_daemon", ""),
                "source_table": row.get("source_table", ""),
                "source_record_id": row.get("source_record_id", ""),
                "visibility": row.get("visibility", "public"),
                "memory_type": row.get("source_table", ""),
                "ingested_at": str(row.get("ingested_at", "")),
            })

        # Apply access control (operator sees everything)
        results = filter_by_access(results, "operator")

        return JSONResponse({"results": results, "count": len(results)})

    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("Memory search failed: %s", exc)
        return JSONResponse({"error": str(exc), "results": []}, status_code=500)


@router.get("/graph")
async def memory_graph(
    center: str | None = Query(None, description="Node ID to center on"),
    depth: int = Query(2, ge=1, le=5, description="Traversal depth"),
    edge_types: str | None = Query(None, description="Comma-separated edge types"),
    source: str | None = Query(None, description="Filter by daemon"),
    limit: int = Query(200, ge=1, le=1000),
) -> JSONResponse:
    """Get graph data for vis.js/cytoscape rendering."""
    if not _explorer_enabled():
        return JSONResponse({"error": "Memory Explorer is disabled"}, status_code=503)

    try:
        from shared.magma import get_graph_view

        parsed_edge_types = edge_types.split(",") if edge_types else None
        nodes, edges = await get_graph_view(
            center_id=center,
            depth=depth,
            edge_types=parsed_edge_types,
            source_filter=source,
            limit=limit,
            requesting_agent="operator",
        )

        # Format for vis.js
        formatted_nodes = [
            {
                "id": n["id"],
                "label": n.get("label", "")[:80],
                "group": n.get("group", "unknown"),
                "type": n.get("type", "unknown"),
                "confidence": n.get("confidence", 0.5),
                "size": max(5, n.get("confidence", 0.5) * 20),
            }
            for n in nodes
        ]
        formatted_edges = [
            {
                "from": e["from"],
                "to": e["to"],
                "type": e.get("type", "TEMPORAL"),
                "weight": e.get("weight", 0),
            }
            for e in edges
        ]

        return JSONResponse({
            "nodes": formatted_nodes,
            "edges": formatted_edges,
            "node_count": len(formatted_nodes),
            "edge_count": len(formatted_edges),
        })

    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("Memory graph failed: %s", exc)
        return JSONResponse({"nodes": [], "edges": [], "error": str(exc)}, status_code=500)


@router.get("/timeline")
async def memory_timeline(
    from_ts: float | None = Query(None, description="Time range start (epoch)"),
    to_ts: float | None = Query(None, description="Time range end (epoch)"),
    sources: str | None = Query(None, description="Comma-separated daemon names"),
    bucket: str = Query("hour", description="Bucket size: hour | day | week"),
) -> JSONResponse:
    """Time-bucketed memory activity per daemon."""
    if not _explorer_enabled():
        return JSONResponse({"error": "Memory Explorer is disabled"}, status_code=503)

    try:
        from shared.magma import get_timeline_view

        time_range = None
        if from_ts or to_ts:
            time_range = (from_ts, to_ts)

        source_list = sources.split(",") if sources else None

        buckets = await get_timeline_view(
            time_range=time_range,
            source_filter=source_list,
            bucket_size=bucket,
            requesting_agent="operator",
        )

        return JSONResponse({"buckets": buckets, "count": len(buckets)})

    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("Memory timeline failed: %s", exc)
        return JSONResponse({"buckets": [], "error": str(exc)}, status_code=500)


@router.get("/economics")
async def memory_economics(
    agent: str | None = Query(None, description="Agent name for balance query"),
) -> JSONResponse:
    """Conway economics panel data (read-only adapter)."""
    if not _explorer_enabled():
        return JSONResponse({"error": "Memory Explorer is disabled"}, status_code=503)

    try:
        from shared.magma import ConwayReadOnlyAdapter

        adapter = ConwayReadOnlyAdapter()

        if agent:
            balance = await adapter.query_balance(agent)
            spending = await adapter.query_spending(agent)
            return JSONResponse({
                "agent": agent,
                "balance": dict(balance) if balance else {},
                "spending": spending,
            })
        else:
            economics = await adapter.query_system_economics()
            return JSONResponse({
                "system": dict(economics) if economics else {},
            })

    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("Memory economics failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/stats")
async def memory_stats() -> JSONResponse:
    """Memory bus statistics — sync state per source table."""
    try:
        from shared.db import fetch_all

        rows = await fetch_all(
            "SELECT source_table, last_ingested_id, last_ingested_at, records_ingested "
            "FROM magma_sync_state ORDER BY source_table"
        )
        stats = [dict(r) for r in rows] if rows else []
        return JSONResponse({"sync_state": stats})
    except (ImportError, ConnectionError, RuntimeError, OSError) as exc:
        logger.warning("Memory stats failed: %s", exc)
        return JSONResponse({"sync_state": [], "error": str(exc)}, status_code=500)
