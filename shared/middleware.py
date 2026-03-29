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
