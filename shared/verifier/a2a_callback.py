"""Layer 3: Daemon-side verifier callback over A2A.

The eng review caught that Layer 3 (external check) CAN'T live in the proxy:
- Ruflo's pytest needs Ruflo's worktree state
- Deerflow's citation round-trip needs Deerflow's source corpus
- Conway's schema check needs Conway's ledger DB
- Titan's brand-voice linter needs Titan's lead context

So Layer 3 is a CALLBACK contract: the proxy receives the candidate, posts it
back to the originating daemon over A2A, and waits for a verdict.

Each daemon registers a verifier function at startup. The daemon decides what
"valid" means for its own task class.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("perseus.verifier.a2a")


@dataclass
class VerificationRequest:
    daemon: str                    # Which daemon should verify
    task_class: str                # Task identifier (e.g. "ruflo.fix_proposal")
    candidate_response: str
    original_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 30.0


@dataclass
class VerificationResult:
    passed: bool
    reason: str = ""
    suggested_fix: str = ""
    layer: str = "L3"
    duration_ms: int = 0
    verifier_name: str = ""


# Type alias for verifier functions
VerifierFn = Callable[[VerificationRequest], Awaitable[VerificationResult]]


class DaemonSideVerifier:
    """In-process registry of verifier callbacks per (daemon, task_class)."""

    def __init__(self):
        self._verifiers: dict[str, VerifierFn] = {}

    def register(
        self,
        daemon: str,
        task_class: str,
        verifier: VerifierFn,
    ) -> None:
        key = f"{daemon}.{task_class}"
        if key in self._verifiers:
            logger.warning("Overwriting existing verifier for %s", key)
        self._verifiers[key] = verifier
        logger.debug("Registered verifier for %s", key)

    def register_default(self, daemon: str, verifier: VerifierFn) -> None:
        """Register a fallback verifier for any task_class on this daemon."""
        self._verifiers[f"{daemon}.*"] = verifier

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        import time
        t0 = time.perf_counter()
        verifier = self._lookup(request.daemon, request.task_class)
        if verifier is None:
            return VerificationResult(
                passed=True,
                reason="no verifier registered, default-pass",
                duration_ms=0,
            )
        try:
            result = await asyncio.wait_for(verifier(request), timeout=request.timeout_seconds)
            result.duration_ms = int((time.perf_counter() - t0) * 1000)
            return result
        except asyncio.TimeoutError:
            return VerificationResult(
                passed=False,
                reason=f"verifier timeout after {request.timeout_seconds}s",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:
            logger.error("Verifier crashed for %s.%s: %s", request.daemon, request.task_class, exc)
            return VerificationResult(
                passed=False,
                reason=f"verifier exception: {exc}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    def _lookup(self, daemon: str, task_class: str) -> VerifierFn | None:
        specific = self._verifiers.get(f"{daemon}.{task_class}")
        if specific:
            return specific
        return self._verifiers.get(f"{daemon}.*")


_default_verifier = DaemonSideVerifier()


def register_verifier(daemon: str, task_class: str, verifier: VerifierFn) -> None:
    _default_verifier.register(daemon, task_class, verifier)


async def request_verification(
    *,
    daemon: str,
    task_class: str,
    candidate_response: str,
    original_prompt: str,
    metadata: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> VerificationResult:
    request = VerificationRequest(
        daemon=daemon,
        task_class=task_class,
        candidate_response=candidate_response,
        original_prompt=original_prompt,
        metadata=metadata or {},
        timeout_seconds=timeout_seconds,
    )
    return await _default_verifier.verify(request)


# ============================================================================
# Built-in default verifiers — daemons can override
# ============================================================================

async def default_json_schema_verifier(request: VerificationRequest) -> VerificationResult:
    """Default L3: verify candidate is valid JSON if schema is provided."""
    import json
    schema = request.metadata.get("expected_schema")
    if not schema:
        return VerificationResult(passed=True, reason="no schema, default-pass")
    try:
        data = json.loads(request.candidate_response)
    except json.JSONDecodeError as exc:
        return VerificationResult(
            passed=False,
            reason=f"JSON parse failed: {exc}",
            suggested_fix="Return valid JSON matching the schema",
        )
    required = schema.get("required", [])
    missing = [k for k in required if k not in data]
    if missing:
        return VerificationResult(
            passed=False,
            reason=f"Missing required keys: {missing}",
        )
    return VerificationResult(passed=True, reason="schema validated")


__all__ = [
    "DaemonSideVerifier",
    "VerificationRequest",
    "VerificationResult",
    "VerifierFn",
    "register_verifier",
    "request_verification",
    "default_json_schema_verifier",
]
