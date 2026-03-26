"""BM25 memory backend — classic term-frequency retrieval."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openjarvis._rust_bridge import get_rust_module
from openjarvis.core.events import EventType, get_event_bus
from openjarvis.core.registry import MemoryRegistry
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult

_rust = get_rust_module()


def _tokenize(text: str) -> List[str]:
    """Lowercase whitespace tokenizer."""
    return text.lower().split()


@MemoryRegistry.register("bm25")
class BM25Memory(MemoryBackend):
    """In-memory BM25 (Okapi) retrieval backend.

    Uses the ``rank_bm25`` library to score documents against a query
    using the classic BM25 probabilistic ranking function.  All data
    lives in memory — there is no persistence across restarts.
    """

    backend_id: str = "bm25"

    def __init__(self) -> None:
        _r = get_rust_module()
        if _r is None:
            self._rust_impl = None
            # Pure-Python fallback: simple in-memory document store
            import uuid as _uuid
            self._docs: Dict[str, Dict[str, Any]] = {}
            self._uuid = _uuid
        else:
            self._rust_impl = _r.BM25Memory()

    # -- ABC implementation -------------------------------------------------

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist *content* and return a unique document id."""
        meta_json = json.dumps(metadata) if metadata else None
        if self._rust_impl is None:
            doc_id = str(self._uuid.uuid4())
            self._docs[doc_id] = {
                "content": content,
                "source": source,
                "metadata": metadata or {},
            }
        else:
            doc_id = self._rust_impl.store(content, source, meta_json)
        bus = get_event_bus()
        bus.publish(EventType.MEMORY_STORE, {
            "backend": self.backend_id,
            "doc_id": doc_id,
            "source": source,
        })
        return doc_id

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Search for *query* and return the top-k results."""
        if not query.strip():
            return []
        if self._rust_impl is None:
            # Simple term-frequency fallback
            terms = _tokenize(query)
            scored = []
            for doc_id, doc in self._docs.items():
                tokens = _tokenize(doc["content"])
                score = sum(tokens.count(t) for t in terms) / max(len(tokens), 1)
                if score > 0:
                    scored.append((score, doc_id, doc))
            scored.sort(key=lambda x: x[0], reverse=True)
            results = [
                RetrievalResult(
                    content=doc["content"],
                    score=score,
                    source=doc["source"],
                    metadata=doc["metadata"],
                )
                for score, _id, doc in scored[:top_k]
            ]
        else:
            from openjarvis._rust_bridge import retrieval_results_from_json
            results = retrieval_results_from_json(
                self._rust_impl.retrieve(query, top_k),
            )
        bus = get_event_bus()
        bus.publish(EventType.MEMORY_RETRIEVE, {
            "backend": self.backend_id,
            "query": query,
            "num_results": len(results),
        })
        return results

    def delete(self, doc_id: str) -> bool:
        """Delete a document by id."""
        if self._rust_impl is None:
            if doc_id in self._docs:
                del self._docs[doc_id]
                return True
            return False
        return self._rust_impl.delete(doc_id)

    def clear(self) -> None:
        """Remove all stored documents."""
        if self._rust_impl is None:
            self._docs.clear()
            return
        self._rust_impl.clear()

    def count(self) -> int:
        """Return the number of stored documents."""
        if self._rust_impl is None:
            return len(self._docs)
        return self._rust_impl.count()


__all__ = ["BM25Memory"]
