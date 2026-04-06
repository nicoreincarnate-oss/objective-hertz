"""Withholding pattern -- suppress recoverable errors until recovery exhausted.

When an LLM call fails with a recoverable error (rate limit, timeout, transient 500),
the buffer does NOT propagate the error to the consumer. Instead, it:
1. Holds back the error
2. Retries via the next step in the fallback chain
3. If recovery succeeds, returns the recovered response seamlessly
4. If all recovery paths fail, THEN raises the final error

This prevents cascading failures in:
- Titan pipeline stages (a mid-stream error would corrupt the state machine)
- A2A agent communication (error would cascade to consuming agent)
- Hermes alert processing (error would lose alert context)

Phase 23 (B-13): Integrates with UnifiedLLMFactory fallback chains.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("perseus.llm.withholding")


class WithholdingBuffer:
    """Suppresses recoverable errors from LLM calls until recovery exhausted."""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def execute_chain_with_withholding(
        self,
        chain: Any,
        prompt: str,
        system: str,
        max_tokens: int,
        temperature: float,
    ) -> Any:
        """Walk fallback chain with error classification.

        - RecoverableError (rate_limit, timeout, 500, 502, 503): suppress, try next step
        - FatalError (auth, invalid_request, 400): raise immediately, do not withhold
        """

        last_error: Exception | None = None
        withheld_errors: list[tuple[int, str, Exception]] = []

        for depth, step in enumerate(chain):
            provider = self._factory._providers.get(step.engine)
            if provider is None:
                continue

            model_id = step.model_id or await self._factory._resolve_model_id(
                step.tier
            )

            try:
                response = await asyncio.wait_for(
                    provider.generate(
                        prompt,
                        system=system,
                        model_id=model_id,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ),
                    timeout=step.timeout_seconds,
                )
                if depth > 0:
                    response.fallback_triggered = True
                    response.fallback_reason = (
                        str(last_error)[:500]
                        if last_error
                        else "previous_unavailable"
                    )
                    response.fallback_depth = depth
                    logger.info(
                        "Withholding recovered at depth %d (%s). "
                        "Withheld %d error(s): %s",
                        depth,
                        step.tier.value,
                        len(withheld_errors),
                        "; ".join(
                            f"depth {d}: {e}" for d, _, e in withheld_errors
                        ),
                    )
                return response

            except Exception as exc:
                if self._is_fatal(exc):
                    logger.error(
                        "Fatal error at depth %d -- NOT withholding: %s",
                        depth,
                        exc,
                    )
                    raise  # Fatal errors surface immediately

                # Recoverable: withhold and try next step
                last_error = exc
                withheld_errors.append((depth, step.tier.value, exc))
                logger.info(
                    "Withholding recoverable error at depth %d (%s): %s",
                    depth,
                    step.tier.value,
                    exc,
                )
                continue

        # All steps exhausted -- surface the accumulated error
        raise RuntimeError(
            f"All {len(chain)} recovery paths exhausted. "
            f"Withheld {len(withheld_errors)} error(s): "
            + "; ".join(f"[{t}] {e}" for _, t, e in withheld_errors)
        )

    @staticmethod
    def _is_fatal(exc: Exception) -> bool:
        """Classify whether an error is fatal (should NOT be withheld)."""
        err_str = str(exc).lower()
        fatal_patterns = [
            "authentication",
            "invalid_api_key",
            "unauthorized",
            "invalid_request",
            "malformed",
            "permission",
        ]
        # 400 is fatal but only when it's the HTTP status, not part of another message
        if "400" in err_str and ("bad request" in err_str or "invalid" in err_str):
            return True
        return any(p in err_str for p in fatal_patterns)

    @staticmethod
    def _is_recoverable(exc: Exception) -> bool:
        """Classify whether an error is recoverable (should be withheld)."""
        err_str = str(exc).lower()
        recoverable_patterns = [
            "rate_limit",
            "429",
            "timeout",
            "timed out",
            "500",
            "502",
            "503",
            "service_unavailable",
            "overloaded",
            "capacity",
            "connection",
        ]
        return any(p in err_str for p in recoverable_patterns)
