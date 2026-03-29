"""Interface contracts for all integration surfaces (Phases 1-7).

Each Protocol is ``@runtime_checkable`` so implementations can be validated
at startup via ``isinstance(obj, SomeProtocol)``.

These contracts define the API boundaries that later phases must implement.
They are intentionally minimal -- concrete implementations may expose
additional methods, but these are the *required* surface areas.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DNAProvider(Protocol):
    """Phase 1: Provides DNA context for LLM calls.

    Implementations supply per-daemon personality context and token budgets
    so that each daemon's LLM interactions reflect its assigned DNA profile.
    """

    def get_dna(self, daemon_name: str) -> str:
        """Return the DNA system-prompt fragment for *daemon_name*."""
        ...

    def get_token_budget(self) -> int:
        """Return the max token budget for DNA-augmented calls."""
        ...


@runtime_checkable
class SlopScorer(Protocol):
    """Phase 2: Scores content quality across 5 dimensions.

    The anti-slop quality gate evaluates generated content for cliches,
    filler, vague claims, and other low-quality patterns.
    """

    async def score(self, content: str, context: str) -> dict[str, float]:
        """Score *content* and return per-dimension scores."""
        ...

    def get_threshold(self, context: str) -> float:
        """Return the minimum acceptable quality threshold for *context*."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Phase 3: Persistent daemon memory.

    Enables daemons to store and retrieve episodic, semantic, and
    procedural memories across restarts.
    """

    async def load(self, daemon_name: str, memory_type: str) -> list[dict]:
        """Load memories of *memory_type* for *daemon_name*."""
        ...

    async def save(self, daemon_name: str, memory_type: str, entry: dict) -> None:
        """Persist a memory *entry* for *daemon_name*."""
        ...

    async def cleanup(self, daemon_name: str) -> int:
        """Remove stale memories for *daemon_name*; return count removed."""
        ...


@runtime_checkable
class Middleware(Protocol):
    """Phase 4: Async middleware for pipeline stages.

    Middleware wraps pipeline stage execution with cross-cutting concerns
    (logging, metrics, error handling, etc.).
    """

    async def __call__(self, ctx: dict[str, Any], next_fn: Any) -> Any:
        """Process *ctx* and call *next_fn* to continue the chain."""
        ...


@runtime_checkable
class ContextRetriever(Protocol):
    """Phase 5: RLM recursive context retrieval.

    Provides retrieval-augmented generation (RAG) with recursive context
    expansion for richer LLM prompts.
    """

    async def retrieve(self, query: str, limit: int) -> list[dict]:
        """Retrieve up to *limit* context entries matching *query*."""
        ...

    async def store(self, content: str, metadata: dict) -> str:
        """Store *content* with *metadata*; return the entry ID."""
        ...


@runtime_checkable
class CryptoProvider(Protocol):
    """Phase 6: Encryption and signing.

    Post-quantum-safe cryptographic operations for securing
    inter-agent communication and stored secrets.
    """

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt *plaintext* and return ciphertext."""
        ...

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt *ciphertext* and return plaintext."""
        ...

    def sign(self, message: bytes) -> bytes:
        """Sign *message* and return the signature."""
        ...

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify *signature* over *message*."""
        ...


@runtime_checkable
class ThresholdProvider(Protocol):
    """Phase 7: Adaptive threshold management.

    Manages dynamic thresholds that adapt based on observed outcomes
    (e.g. email open rates, quality scores).
    """

    def get_threshold(self, name: str) -> float:
        """Return the current threshold value for *name*."""
        ...

    def update(self, name: str, outcome: float) -> None:
        """Update the threshold for *name* based on an observed *outcome*."""
        ...


__all__ = [
    "ContextRetriever",
    "CryptoProvider",
    "DNAProvider",
    "MemoryStore",
    "Middleware",
    "SlopScorer",
    "ThresholdProvider",
]
