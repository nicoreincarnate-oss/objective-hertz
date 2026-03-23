"""MemoryFederation — federated search across all memory backends and vassals.

Queries:
1. Local OpenJarvis backends (FAISS, ColBERT, BM25, SQLite, KG)
2. Vassal memory via A2A (Postgres, Qdrant, Mem0)

Merges, deduplicates, and ranks results from all sources.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FederatedResult:
    """A single result from any memory backend."""

    content: str
    source_node: str = ""       # "local", "titan", "hermes", "clawdbot"
    source_backend: str = ""    # "faiss", "postgres", "qdrant", "mem0", etc.
    confidence: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.md5(self.content.encode()).hexdigest()


class MemoryFederation:
    """Federated memory search across local backends and A2A vassals.

    Parameters
    ----------
    local_backends:
        List of local memory backend instances (must have ``retrieve(query, top_k)``).
    vassal_discovery:
        VassalDiscovery instance for querying remote memory.
    merge_strategy:
        How to rank merged results: "confidence_weighted", "round_robin", "source_diverse".
    """

    def __init__(
        self,
        local_backends: Optional[List[Any]] = None,
        vassal_discovery: Optional[Any] = None,
        merge_strategy: str = "confidence_weighted",
    ) -> None:
        self._local_backends = local_backends or []
        self._vassals = vassal_discovery
        self._merge_strategy = merge_strategy

    # ── Search ────────────────────────────────────────────────────────

    async def search(self, query: str, top_k: int = 10) -> List[FederatedResult]:
        """Search ALL memory — local backends + every vassal."""
        tasks = []

        # Local backends
        for backend in self._local_backends:
            tasks.append(self._query_local(backend, query, top_k))

        # Vassal memory
        if self._vassals:
            for name, vassal in self._vassals.vassals.items():
                if vassal.healthy:
                    tasks.append(self._query_vassal(name, query, top_k))

        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten
        merged: List[FederatedResult] = []
        for result_set in all_results:
            if isinstance(result_set, Exception):
                logger.debug("Memory search error: %s", result_set)
                continue
            merged.extend(result_set)

        # Deduplicate + rank
        return self._merge(merged, top_k)

    async def _query_local(self, backend: Any, query: str, top_k: int) -> List[FederatedResult]:
        """Query a local memory backend."""
        try:
            results = backend.retrieve(query, top_k)
            return [
                FederatedResult(
                    content=getattr(r, "content", str(r)),
                    source_node="local",
                    source_backend=type(backend).__name__.lower(),
                    confidence=getattr(r, "score", 0.5),
                    metadata=getattr(r, "metadata", {}),
                )
                for r in results
            ]
        except Exception as exc:
            logger.debug("Local backend %s failed: %s", type(backend).__name__, exc)
            return []

    async def _query_vassal(self, name: str, query: str, top_k: int) -> List[FederatedResult]:
        """Query a vassal's memory via A2A."""
        results: List[FederatedResult] = []

        # Try memory_search capability
        try:
            raw = self._vassals.call(name, "memory_search", query=query, limit=top_k)
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    content = item.get("memory", item.get("content", item.get("insight", str(item))))
                    results.append(FederatedResult(
                        content=content,
                        source_node=name,
                        source_backend="mem0/qdrant",
                        confidence=item.get("confidence", 0.5),
                        metadata=item,
                    ))
        except Exception:
            pass

        # Try learnings_query capability (Titan-specific)
        try:
            raw = self._vassals.call(name, "learnings_query", limit=top_k)
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    results.append(FederatedResult(
                        content=item.get("insight", str(item)),
                        source_node=name,
                        source_backend="titan_learnings",
                        confidence=item.get("confidence", 0.5),
                        metadata=item,
                        created_at=str(item.get("created_at", "")),
                    ))
        except Exception:
            pass

        return results

    # ── Merge & rank ──────────────────────────────────────────────────

    def _merge(self, results: List[FederatedResult], top_k: int) -> List[FederatedResult]:
        """Deduplicate and rank results."""
        # Deduplicate by content hash
        seen = set()
        unique = []
        for r in results:
            h = r.content_hash
            if h not in seen:
                seen.add(h)
                unique.append(r)

        if self._merge_strategy == "confidence_weighted":
            unique.sort(key=lambda r: r.confidence, reverse=True)
        elif self._merge_strategy == "source_diverse":
            unique = self._source_diverse_rank(unique)
        # round_robin: interleave sources
        elif self._merge_strategy == "round_robin":
            unique = self._round_robin(unique)

        return unique[:top_k]

    def _source_diverse_rank(self, results: List[FederatedResult]) -> List[FederatedResult]:
        """Rank favoring diversity of sources."""
        by_source: Dict[str, List[FederatedResult]] = {}
        for r in results:
            key = f"{r.source_node}:{r.source_backend}"
            by_source.setdefault(key, []).append(r)

        # Sort each source by confidence
        for items in by_source.values():
            items.sort(key=lambda r: r.confidence, reverse=True)

        # Interleave: take top from each source in round-robin
        ranked = []
        while any(by_source.values()):
            for key in list(by_source.keys()):
                if by_source[key]:
                    ranked.append(by_source[key].pop(0))
                else:
                    del by_source[key]
        return ranked

    def _round_robin(self, results: List[FederatedResult]) -> List[FederatedResult]:
        """Simple round-robin across source nodes."""
        by_node: Dict[str, List[FederatedResult]] = {}
        for r in results:
            by_node.setdefault(r.source_node, []).append(r)
        ranked = []
        while any(by_node.values()):
            for key in list(by_node.keys()):
                if by_node[key]:
                    ranked.append(by_node[key].pop(0))
                else:
                    del by_node[key]
        return ranked

    # ── Store ─────────────────────────────────────────────────────────

    async def store(
        self,
        content: str,
        target: str = "local",
        category: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Store memory to a specific target.

        target: "local" (first local backend), or vassal name ("titan", "hermes").
        """
        if target == "local" and self._local_backends:
            try:
                backend = self._local_backends[0]
                backend.store(content, source="openjarvis", metadata=metadata or {})
                return True
            except Exception as exc:
                logger.warning("Local memory store failed: %s", exc)
                return False

        if self._vassals:
            try:
                self._vassals.call(target, "memory_store", content=content, category=category)
                return True
            except Exception as exc:
                logger.warning("Vassal %s memory store failed: %s", target, exc)
                return False

        return False

    # ── Info ──────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Return summary of all available memory backends."""
        local = [type(b).__name__ for b in self._local_backends]
        remote = {}
        if self._vassals:
            for name, v in self._vassals.vassals.items():
                remote[name] = {"healthy": v.healthy, "capabilities": v.capabilities}
        return {
            "local_backends": local,
            "remote_nodes": remote,
            "merge_strategy": self._merge_strategy,
        }


__all__ = ["FederatedResult", "MemoryFederation"]
