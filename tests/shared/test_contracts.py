"""Tests for shared.contracts Protocol types (Phase 0b)."""

from __future__ import annotations

from typing import Any

import pytest

from shared.contracts import (
    ContextRetriever,
    CryptoProvider,
    DNAProvider,
    MemoryStore,
    Middleware,
    SlopScorer,
    ThresholdProvider,
)

# ---------------------------------------------------------------------------
# Conforming implementations (minimal stubs that satisfy each Protocol)
# ---------------------------------------------------------------------------


class _GoodDNA:
    def get_dna(self, daemon_name: str) -> str:
        return "dna"

    def get_token_budget(self) -> int:
        return 1000


class _GoodSlop:
    async def score(self, content: str, context: str) -> dict[str, float]:
        return {"quality": 0.9}

    def get_threshold(self, context: str) -> float:
        return 0.5


class _GoodMemory:
    async def load(self, daemon_name: str, memory_type: str) -> list[dict]:
        return []

    async def save(self, daemon_name: str, memory_type: str, entry: dict) -> None:
        pass

    async def cleanup(self, daemon_name: str) -> int:
        return 0


class _GoodMiddleware:
    async def __call__(self, ctx: dict[str, Any], next_fn: Any) -> Any:
        return await next_fn(ctx)


class _GoodRetriever:
    async def retrieve(self, query: str, limit: int) -> list[dict]:
        return []

    async def store(self, content: str, metadata: dict) -> str:
        return "id-1"


class _GoodCrypto:
    def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext

    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext

    def sign(self, message: bytes) -> bytes:
        return b"sig"

    def verify(self, message: bytes, signature: bytes) -> bool:
        return True


class _GoodThreshold:
    def get_threshold(self, name: str) -> float:
        return 0.7

    def update(self, name: str, outcome: float) -> None:
        pass


# ---------------------------------------------------------------------------
# Non-conforming classes (missing required methods)
# ---------------------------------------------------------------------------


class _Empty:
    """Has no methods at all."""

    pass


class _PartialDNA:
    """Only has get_dna, missing get_token_budget."""

    def get_dna(self, daemon_name: str) -> str:
        return "dna"


class _WrongSignatureCrypto:
    """Has all method names but wrong signatures (missing params)."""

    def encrypt(self) -> bytes:  # type: ignore[override]
        return b""

    def decrypt(self) -> bytes:  # type: ignore[override]
        return b""

    def sign(self) -> bytes:  # type: ignore[override]
        return b""

    def verify(self) -> bool:  # type: ignore[override]
        return True


# ---------------------------------------------------------------------------
# Tests: each Protocol validates conforming implementations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "protocol, impl_cls",
    [
        (DNAProvider, _GoodDNA),
        (SlopScorer, _GoodSlop),
        (MemoryStore, _GoodMemory),
        (Middleware, _GoodMiddleware),
        (ContextRetriever, _GoodRetriever),
        (CryptoProvider, _GoodCrypto),
        (ThresholdProvider, _GoodThreshold),
    ],
)
def test_conforming_implementations(protocol, impl_cls):
    """Conforming implementation passes isinstance check."""
    assert isinstance(impl_cls(), protocol)


@pytest.mark.parametrize(
    "protocol",
    [
        DNAProvider,
        SlopScorer,
        MemoryStore,
        Middleware,
        ContextRetriever,
        CryptoProvider,
        ThresholdProvider,
    ],
)
def test_empty_class_rejected(protocol):
    """Empty class fails isinstance check for all protocols."""
    assert not isinstance(_Empty(), protocol)


def test_partial_dna_rejected():
    """Class with only some methods is rejected."""
    assert not isinstance(_PartialDNA(), DNAProvider)


def test_all_protocols_are_runtime_checkable():
    """Every exported Protocol has @runtime_checkable decorator."""
    for proto in [
        DNAProvider,
        SlopScorer,
        MemoryStore,
        Middleware,
        ContextRetriever,
        CryptoProvider,
        ThresholdProvider,
    ]:
        # runtime_checkable protocols support isinstance
        assert hasattr(proto, "__protocol_attrs__") or hasattr(
            proto, "_is_runtime_protocol"
        ), f"{proto.__name__} is not runtime_checkable"
