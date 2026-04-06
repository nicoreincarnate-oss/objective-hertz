"""Shadow comparator for 48h parallel run gate.

Runs old and new LLM paths in parallel:
- Old path (LLMClient) is PRIMARY -- its result is returned to caller
- New path (UnifiedLLMFactory) runs as fire-and-forget background task
- Comparison metrics logged to llm_shadow_comparison table

Phase 23: Required for the 48h validation gate before cutover.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("perseus.llm.shadow")


class ShadowComparator:
    """Runs old and new LLM paths in parallel, compares results."""

    def __init__(self) -> None:
        self._comparison_count: int = 0
        self._error_delta_count: int = 0
        self._latency_deltas: list[float] = []
        self._cost_deltas: list[float] = []

    async def compare(
        self,
        old_fn: Callable,
        new_fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Run both paths. Return old_fn result. Log new_fn delta asynchronously."""
        old_t0 = time.perf_counter()
        result = await old_fn(*args, **kwargs)
        old_latency_ms = int((time.perf_counter() - old_t0) * 1000)

        # New path runs in background -- does NOT block the response
        asyncio.create_task(
            self._compare_shadow(new_fn, result, old_latency_ms, *args, **kwargs)
        )
        return result

    async def _compare_shadow(
        self,
        new_fn: Callable,
        old_result: str,
        old_latency_ms: int,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Execute new path and record comparison metrics."""
        try:
            new_t0 = time.perf_counter()
            new_result = await new_fn(*args, **kwargs)
            new_latency_ms = int((time.perf_counter() - new_t0) * 1000)
            self._comparison_count += 1

            latency_delta = new_latency_ms - old_latency_ms
            self._latency_deltas.append(latency_delta)

            # Log comparison to DB
            try:
                from shared.db import execute

                await execute(
                    """INSERT INTO llm_shadow_comparison
                       (tier, old_latency_ms, new_latency_ms, old_content_length,
                        new_content_length, new_fallback_triggered, new_fallback_depth)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        kwargs.get("model", "fast"),
                        old_latency_ms,
                        new_latency_ms,
                        len(old_result),
                        len(new_result),
                        False,
                        0,
                    ),
                )
            except Exception as db_exc:
                logger.debug("Shadow DB write failed: %s", db_exc)

        except Exception as exc:
            self._error_delta_count += 1
            logger.warning("Shadow path error: %s", exc)
            try:
                from shared.db import execute

                await execute(
                    """INSERT INTO llm_shadow_comparison
                       (tier, old_latency_ms, new_error)
                       VALUES (%s, %s, %s)""",
                    (
                        kwargs.get("model", "fast"),
                        old_latency_ms,
                        str(exc)[:500],
                    ),
                )
            except Exception:
                pass

    def is_ready_for_cutover(self) -> bool:
        """Check if 48h gate criteria are met."""
        if self._comparison_count < 100:
            return False  # Not enough data
        error_rate = self._error_delta_count / max(self._comparison_count, 1)
        if error_rate >= 0.01:
            return False  # Error delta >= 1%

        # Check latency P95 regression
        if self._latency_deltas:
            sorted_deltas = sorted(self._latency_deltas)
            p95_idx = int(len(sorted_deltas) * 0.95)
            p95_delta = sorted_deltas[min(p95_idx, len(sorted_deltas) - 1)]
            if p95_delta > 200:
                return False  # P95 latency regression > 200ms

        return True

    @property
    def stats(self) -> dict[str, Any]:
        """Return current comparison statistics."""
        error_rate = (
            self._error_delta_count / max(self._comparison_count, 1)
            if self._comparison_count > 0
            else 0.0
        )
        return {
            "comparison_count": self._comparison_count,
            "error_delta_count": self._error_delta_count,
            "error_rate": round(error_rate, 4),
            "ready_for_cutover": self.is_ready_for_cutover(),
        }
