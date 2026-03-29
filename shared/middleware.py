"""DeerFlow Async Middleware Chain — composable cross-cutting concerns for pipelines.

Feature flag: ENABLE_MIDDLEWARE env var gates the entire chain.
Individual middleware functions are gated by their own feature flags.

Middleware signature: async def mw(ctx: dict, next_fn: NextFn) -> StageResult
Chain uses recursive index-based compose pattern (outermost = first added).

Implements the Middleware Protocol from shared.contracts.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("perseus.middleware")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

StageResult = dict[str, Any]
NextFn = Callable[[dict[str, Any]], Awaitable[StageResult]]


# ---------------------------------------------------------------------------
# Feature flag helper
# ---------------------------------------------------------------------------


def _flag(name: str) -> bool:
    """Check if an env-based feature flag is active."""
    return os.environ.get(name, "").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Task 1: MiddlewareChain core (MW-01)
# ---------------------------------------------------------------------------


class MiddlewareChain:
    """Ordered chain of async middleware functions.

    Each middleware receives ``(ctx, next_fn)`` and must call ``next_fn(ctx)``
    to continue the chain.  The first middleware added is the outermost wrapper.
    """

    def __init__(self) -> None:
        self._middlewares: list[Callable[..., Awaitable[StageResult]]] = []

    def use(self, middleware: Callable[..., Awaitable[StageResult]]) -> MiddlewareChain:
        """Add *middleware* to the chain.  Returns ``self`` for chaining."""
        self._middlewares.append(middleware)
        return self

    async def execute(self, ctx: dict[str, Any], handler: NextFn) -> StageResult:
        """Execute the middleware chain around *handler*."""

        async def _compose(index: int, c: dict[str, Any]) -> StageResult:
            if index >= len(self._middlewares):
                return await handler(c)
            mw = self._middlewares[index]
            return await mw(c, lambda inner_c: _compose(index + 1, inner_c))

        return await _compose(0, ctx)


# ---------------------------------------------------------------------------
# Task 2: Per-pipeline middleware config (MW-02, MW-08)
# ---------------------------------------------------------------------------

# Default ordered stacks per pipeline
TITAN_MIDDLEWARE = [
    "budget_check",
    "dna_guard",
    "anti_slop",
    "memory",
    "telemetry",
]

PIPELINE_CONFIGS: dict[str, list[str]] = {
    "titan": list(TITAN_MIDDLEWARE),
    "clawdbot": ["budget_check", "dna_guard", "anti_slop", "telemetry"],
    "hermes": ["budget_check", "dna_guard", "telemetry"],
    "perseus": ["budget_check", "telemetry"],
}


def _validate_ordering(names: list[str], pipeline: str) -> None:
    """Warn if anti_slop appears before dna_guard (DNA should filter first)."""
    if "anti_slop" in names and "dna_guard" in names:
        if names.index("anti_slop") < names.index("dna_guard"):
            logger.warning(
                "Pipeline '%s': anti_slop before dna_guard — DNA should filter first",
                pipeline,
            )


def build_chain(pipeline_name: str) -> MiddlewareChain:
    """Build a middleware chain for *pipeline_name*.

    Ordering can be overridden via env var ``{PIPELINE}_MIDDLEWARE_ORDER``
    (comma-separated middleware names).  Falls back to ``PIPELINE_CONFIGS``.
    """
    env_key = f"{pipeline_name.upper()}_MIDDLEWARE_ORDER"
    env_val = os.environ.get(env_key, "").strip()

    if env_val:
        names = [n.strip() for n in env_val.split(",") if n.strip()]
        logger.info("Using env override for %s middleware: %s", pipeline_name, names)
    else:
        names = PIPELINE_CONFIGS.get(pipeline_name, ["telemetry"])

    _validate_ordering(names, pipeline_name)

    chain = MiddlewareChain()
    for name in names:
        mw = MIDDLEWARE_REGISTRY.get(name)
        if mw is None:
            logger.warning("Unknown middleware '%s' in pipeline '%s' — skipping", name, pipeline_name)
            continue
        chain.use(mw)
    return chain


# Registry populated below after middleware functions are defined
MIDDLEWARE_REGISTRY: dict[str, Callable[..., Awaitable[StageResult]]] = {}
