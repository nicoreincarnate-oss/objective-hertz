"""Semantic cache — Phase 42.

Redis-backed semantic cache for LLM responses with a STRICT operation allowlist.

Critical safety invariant: NEVER cache code generation. The variance is too high
and a wrong cached patch is far more expensive than an extra API call. The
allowlist is default-deny — any operation not explicitly listed is bypassed.

Cache hit rate target: >20% on lead research summaries, >30% on FAQ-style lookups.
Expected savings: 30-50% on cacheable operations only (not 30-50% globally).

Embedding model: OpenAI text-embedding-3-small ($0.02/M tokens) — cheap enough
to embed every cacheable prompt without budget concerns.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("perseus.semantic_cache")


# ============================================================================
# PORT-PLAN decision #3: optional Redis backend + in-process LRU fallback
# ============================================================================
#
# If REDIS_URL is set AND the server is reachable, _CACHE_BACKEND = "redis"
# and the full SemanticCache (HNSW vector search) is used.
#
# Otherwise we fall back to an in-process LRU keyed by SHA-256(prompt+operation).
# The LRU path is EXACT MATCH ONLY — no semantic similarity — because
# functools.lru_cache has no vector search. That's acceptable per decision #3:
# the semantic HNSW path only works with Redis Stack anyway, so when Redis is
# absent we still get some wins on repeated identical prompts (FAQ, doc_qa,
# industry_classification) without the embedding round-trip.

_REDIS_URL = os.environ.get("REDIS_URL")
_redis_client = None
_CACHE_BACKEND = "lru"

if _REDIS_URL:
    try:
        import redis  # type: ignore

        _redis_client = redis.from_url(_REDIS_URL, socket_connect_timeout=2)
        _redis_client.ping()
        _CACHE_BACKEND = "redis"
    except Exception as _exc:  # noqa: BLE001 — log once, degrade gracefully
        logging.getLogger("shared.semantic_cache").warning(
            "REDIS_URL set but connection failed (%s); "
            "falling back to in-process LRU cache",
            _exc,
        )
        _CACHE_BACKEND = "lru"
        _redis_client = None


@functools.lru_cache(maxsize=1024)
def _lru_lookup(cache_key: str) -> str | None:
    """In-process exact-match cache (fallback when Redis unavailable).

    Keys are SHA-256(operation + '|' + prompt). Values are stored via
    _lru_store() which mutates the lru_cache through an internal dict shim.
    Since lru_cache doesn't support external writes, we wrap it below.
    """
    return None  # populated via _LRU_STORE dict; this is just the plumbing shell


# Backing dict for the LRU fallback (bounded manually since lru_cache can't
# be externally populated). Ring-buffer eviction at 1024 entries.
_LRU_STORE: dict[str, str] = {}
_LRU_MAXSIZE = 1024


def _lru_get(cache_key: str) -> str | None:
    return _LRU_STORE.get(cache_key)


def _lru_set(cache_key: str, value: str) -> None:
    if len(_LRU_STORE) >= _LRU_MAXSIZE:
        # Evict oldest (dict preserves insertion order in Py3.7+)
        try:
            oldest = next(iter(_LRU_STORE))
            del _LRU_STORE[oldest]
        except StopIteration:
            pass
    _LRU_STORE[cache_key] = value


def _make_lru_key(prompt: str, operation: str) -> str:
    return hashlib.sha256(f"{operation}|{prompt}".encode()).hexdigest()


# ============================================================================
# Operation allowlist — default deny
# ============================================================================

CACHEABLE_OPERATIONS: frozenset[str] = frozenset({
    "lead_research_summary",       # Same company → same summary (Titan stage 2)
    "doc_qa",                       # Doc Q&A lookups (Deerflow)
    "industry_classification",      # Lead industry tagging
    "tone_analysis",                # Email tone scoring (deterministic per text)
    "translation",                  # Translation is deterministic
    "summarization_short",          # Short summaries (≤200 tokens output)
    "faq_answer",                   # FAQ Q&A
    "company_size_estimation",      # Lead enrichment
    "url_classification",           # Source scoring (Deerflow)
    "log_summarization",            # Perseus log triage
})

CACHE_FORBIDDEN_OPERATIONS: frozenset[str] = frozenset({
    "code_generation",
    "site_section_generation",
    "html_scaffolding",
    "css_generation",
    "email_compose",                 # Personalization required, NEVER cache
    "outreach_draft",
    "proposal_generation",
    "negotiation",
    "architecture_decision",
    "tool_call",                     # State-dependent
    "agent_loop_step",
    "patch_generation",              # Ruflo
    "fix_proposal",                  # Ruflo
    "workflow_synthesis",            # Openjarvis
    "mega_plan",
    "aider_architect",
    "aider_editor",
})


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    bypasses: int = 0      # Operations not in allowlist
    estimated_cost_saved_usd: float = 0.0
    bytes_used: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class SemanticCache:
    """Redis-backed semantic cache with HNSW vector search.

    Uses OpenAI text-embedding-3-small to compute prompt embeddings and stores
    them in a Redis HNSW index for fast similarity search.

    Default similarity threshold: 0.92 (cosine). Anything above is a "hit".
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        similarity_threshold: float = 0.92,
        ttl_seconds: int = 86400,  # 24h
        embedding_model: str = "text-embedding-3-small",
        embedding_dim: int = 1536,
        index_name: str = "perseus_semantic_cache",
    ):
        self.redis = redis_client
        self.threshold = similarity_threshold
        self.ttl = ttl_seconds
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.index_name = index_name
        self.stats = CacheStats()
        self._index_initialized = False

    async def get(
        self,
        prompt: str,
        operation: str,
        *,
        daemon: str = "",
    ) -> str | None:
        """Try to retrieve a cached response.

        Returns None for any operation in CACHE_FORBIDDEN_OPERATIONS or not in
        CACHEABLE_OPERATIONS (default deny).
        """
        if operation in CACHE_FORBIDDEN_OPERATIONS:
            self.stats.bypasses += 1
            logger.debug("Cache BYPASS (forbidden): %s", operation)
            return None
        if operation not in CACHEABLE_OPERATIONS:
            self.stats.bypasses += 1
            logger.debug("Cache BYPASS (not in allowlist): %s", operation)
            return None

        # PORT-PLAN decision #3: LRU fallback when no Redis client injected.
        if self.redis is None:
            if _CACHE_BACKEND == "lru":
                hit = _lru_get(_make_lru_key(prompt, operation))
                if hit is not None:
                    self.stats.hits += 1
                    self.stats.estimated_cost_saved_usd += 0.05
                    logger.info(
                        "Cache HIT (LRU exact): op=%s daemon=%s", operation, daemon,
                    )
                    return hit
                self.stats.misses += 1
            return None

        try:
            await self._ensure_index()
            embedding = await self._embed(prompt)
            result = await self._knn_search(embedding, operation, k=1)
            if result and result["score"] >= self.threshold:
                self.stats.hits += 1
                self.stats.estimated_cost_saved_usd += 0.05  # Heuristic per hit
                logger.info(
                    "Cache HIT: op=%s daemon=%s similarity=%.3f",
                    operation, daemon, result["score"],
                )
                return result["response"]
            self.stats.misses += 1
            return None
        except Exception as exc:
            logger.warning("Semantic cache get() failed: %s", exc)
            return None

    async def set(
        self,
        prompt: str,
        response: str,
        operation: str,
        *,
        daemon: str = "",
        cost_saved_usd: float = 0.0,
    ) -> None:
        """Store a prompt → response pair with embedding.

        No-op if operation is not cacheable.
        """
        if operation not in CACHEABLE_OPERATIONS:
            return
        if operation in CACHE_FORBIDDEN_OPERATIONS:
            return

        # PORT-PLAN decision #3: LRU fallback write path.
        if self.redis is None:
            if _CACHE_BACKEND == "lru":
                _lru_set(_make_lru_key(prompt, operation), response)
                self.stats.bytes_used += len(response)
            return

        try:
            await self._ensure_index()
            embedding = await self._embed(prompt)
            key = self._make_key(prompt, operation)
            payload = {
                "operation": operation,
                "daemon": daemon,
                "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest(),
                "response": response,
                "stored_at": int(time.time()),
                "cost_saved_estimate": cost_saved_usd,
            }
            await self._store(key, embedding, payload)
            self.stats.bytes_used += len(response)
        except Exception as exc:
            logger.warning("Semantic cache set() failed: %s", exc)

    async def stats_snapshot(self) -> dict[str, Any]:
        return {
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "bypasses": self.stats.bypasses,
            "hit_rate": self.stats.hit_rate,
            "estimated_cost_saved_usd": round(self.stats.estimated_cost_saved_usd, 2),
            "bytes_used": self.stats.bytes_used,
        }

    async def clear(self, operation: str | None = None) -> int:
        """Clear cache entries. If operation is None, clears all entries."""
        if self.redis is None:
            return 0
        if operation is None:
            keys = await self.redis.keys(f"{self.index_name}:*")
        else:
            keys = await self.redis.keys(f"{self.index_name}:{operation}:*")
        if keys:
            await self.redis.delete(*keys)
        return len(keys)

    # ─── Internal ────────────────────────────────────────────────────────

    async def _ensure_index(self) -> None:
        """Create the HNSW index if it doesn't exist (idempotent)."""
        if self._index_initialized:
            return
        try:
            from redis.commands.search.field import TagField, TextField, VectorField
            from redis.commands.search.indexDefinition import IndexDefinition, IndexType
            schema = (
                VectorField(
                    "embedding",
                    "HNSW",
                    {"TYPE": "FLOAT32", "DIM": self.embedding_dim, "DISTANCE_METRIC": "COSINE"},
                ),
                TagField("operation"),
                TagField("daemon"),
                TextField("response"),
            )
            try:
                await self.redis.ft(self.index_name).create_index(
                    fields=schema,
                    definition=IndexDefinition(
                        prefix=[f"{self.index_name}:"], index_type=IndexType.HASH
                    ),
                )
            except Exception:
                pass  # Index already exists
            self._index_initialized = True
        except ImportError:
            logger.warning("redis-py search extras not installed; cache disabled")

    async def _embed(self, text: str) -> list[float]:
        """Get embedding via OpenAI API."""
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise RuntimeError("openai package required for semantic cache")
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.embeddings.create(
            model=self.embedding_model,
            input=text[:8000],  # Truncate to avoid API limits
        )
        return response.data[0].embedding

    async def _knn_search(
        self,
        embedding: list[float],
        operation: str,
        k: int = 1,
    ) -> dict | None:
        """KNN search for the most similar cached prompt."""
        try:
            from redis.commands.search.query import Query
            import numpy as np
            vec = np.array(embedding, dtype=np.float32).tobytes()
            query_str = f"(@operation:{{{operation}}})=>[KNN {k} @embedding $vec AS score]"
            q = Query(query_str).sort_by("score").return_fields("response", "score").dialect(2)
            result = await self.redis.ft(self.index_name).search(q, query_params={"vec": vec})
            if not result.docs:
                return None
            doc = result.docs[0]
            return {
                "response": doc.response,
                "score": 1.0 - float(doc.score),  # Cosine distance → similarity
            }
        except Exception as exc:
            logger.debug("KNN search error: %s", exc)
            return None

    async def _store(self, key: str, embedding: list[float], payload: dict) -> None:
        try:
            import numpy as np
            vec_bytes = np.array(embedding, dtype=np.float32).tobytes()
            mapping = {
                "embedding": vec_bytes,
                "operation": payload["operation"],
                "daemon": payload["daemon"],
                "response": payload["response"],
            }
            await self.redis.hset(key, mapping=mapping)
            await self.redis.expire(key, self.ttl)
        except Exception as exc:
            logger.debug("store error: %s", exc)

    def _make_key(self, prompt: str, operation: str) -> str:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return f"{self.index_name}:{operation}:{prompt_hash}"


__all__ = ["SemanticCache", "CacheStats", "CACHEABLE_OPERATIONS", "CACHE_FORBIDDEN_OPERATIONS"]
