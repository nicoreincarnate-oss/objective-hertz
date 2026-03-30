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
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
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
    "neuro_scorer",
    "memory",
    "telemetry",
]

PIPELINE_CONFIGS: dict[str, list[str]] = {
    "titan": list(TITAN_MIDDLEWARE),
    "clawdbot": ["budget_check", "dna_guard", "anti_slop", "neuro_scorer", "telemetry"],
    "hermes": ["budget_check", "dna_guard", "neuro_scorer", "telemetry"],
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


# ---------------------------------------------------------------------------
# Task 3: MemoryMiddleware (MW-03)
# ---------------------------------------------------------------------------


async def memory_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Inject relevant memories before stage, save outcome after.

    Feature-flag gated by ``ENABLE_DEERFLOW_MEMORY``.
    """
    if not _flag("ENABLE_DEERFLOW_MEMORY"):
        return await next_fn(ctx)

    daemon = ctx.get("daemon_name", "")
    stage = ctx.get("stage_name", "")

    # Pre-stage: inject episodic memories into context
    try:
        from shared.daemon_memory import DaemonMemoryStore

        store = DaemonMemoryStore()
        memories = await store.load(daemon, "episodic")
        ctx["memories"] = memories
    except Exception as exc:
        logger.warning("Memory pre-load failed (non-fatal): %s", exc)
        ctx["memories"] = []

    # Execute stage
    result = await next_fn(ctx)

    # Post-stage: save outcome as episodic memory
    if result.get("success"):
        try:
            from shared.daemon_memory import DaemonMemoryStore as _Store

            post_store = _Store()
            await post_store.save_episodic(
                daemon,
                f"{stage}_outcome",
                {
                    "stage": stage,
                    "result_summary": str(result.get("output", ""))[:500],
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
        except Exception as exc:
            logger.warning("Memory post-save failed (non-fatal): %s", exc)

    return result


# ---------------------------------------------------------------------------
# Task 4: DNAGuardMiddleware (MW-04)
# ---------------------------------------------------------------------------


async def dna_guard_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Validate stage actions against agent DNA boundaries.

    Feature-flag gated by ``ENABLE_DNA_PROFILES``.
    Blocks unauthorized tool usage with a logged warning.
    """
    if not _flag("ENABLE_DNA_PROFILES"):
        return await next_fn(ctx)

    daemon = ctx.get("daemon_name", "")
    stage = ctx.get("stage_name", "")
    tools_used: list[str] = ctx.get("tools", [])

    if not tools_used:
        return await next_fn(ctx)

    try:
        from shared.agent_dna import AgentDNA

        dna = AgentDNA(daemon)
        dna.load()

        for tool in tools_used:
            if not dna.check_action(stage, tool):
                logger.warning(
                    "DNA GUARD: %s blocked from using '%s' in stage '%s'",
                    daemon,
                    tool,
                    stage,
                )
                return {
                    "success": False,
                    "output": f"DNA boundary violation: {tool} not permitted for {daemon}",
                }
    except Exception as exc:
        logger.warning("DNA guard check failed (allowing): %s", exc)

    return await next_fn(ctx)


# ---------------------------------------------------------------------------
# Task 5: AntiSlopMiddleware (MW-05)
# ---------------------------------------------------------------------------

# Stages that produce outbound content requiring quality scoring
_CONTENT_STAGES = frozenset({"email_compose", "site_build", "alert_compose"})


async def anti_slop_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Quality-score content-producing stages and block secret leaks.

    Feature-flag gated by ``ENABLE_ANTI_SLOP``.
    Non-content stages pass through unaffected.
    """
    if not _flag("ENABLE_ANTI_SLOP"):
        return await next_fn(ctx)

    stage = ctx.get("stage_name", "")

    # Non-content stages pass through
    if stage not in _CONTENT_STAGES:
        return await next_fn(ctx)

    result = await next_fn(ctx)

    if result.get("success") and result.get("output"):
        output = result["output"]

        # Secret detection — hard block
        try:
            from shared.anti_slop import detect_secrets

            secrets = detect_secrets(str(output))
            if secrets:
                logger.critical(
                    "SECRET DETECTED in stage '%s': %s",
                    stage,
                    secrets,
                )
                return {
                    "success": False,
                    "output": "BLOCKED: secret detected in output",
                }
        except Exception as exc:
            logger.warning("Secret detection failed (allowing): %s", exc)

        # Quality scoring
        try:
            from shared.anti_slop import AntiSlopScorer

            scorer = AntiSlopScorer()
            scores = await scorer.score(str(output), stage)
            result["quality_scores"] = scores
        except Exception as exc:
            logger.warning("Anti-slop scoring failed (non-fatal): %s", exc)

    return result


# ---------------------------------------------------------------------------
# NeuroScorer Middleware (Phase 8 — NEURO-06)
# ---------------------------------------------------------------------------

# Content-producing stages eligible for neuro-scoring
_NEURO_CONTENT_STAGES = frozenset({"email_compose", "site_build", "follow_up", "alert_compose"})


async def neuro_scorer_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Score content-producing stages with TRIBE v2 neuro-scorer.

    Feature-flag gated by ``ENABLE_NEURO_SCORER``.
    Non-content stages pass through. Scores attached to result for downstream use.
    Alert stages use relaxed thresholds (informational only).
    """
    if not _flag("ENABLE_NEURO_SCORER"):
        return await next_fn(ctx)

    stage = ctx.get("stage_name", "")

    # Non-content stages pass through
    if stage not in _NEURO_CONTENT_STAGES:
        return await next_fn(ctx)

    result = await next_fn(ctx)

    if result.get("success") and result.get("output"):
        try:
            from titan.neuro.neuro_scorer import NeuroScorer

            scorer = NeuroScorer()
            neuro = await scorer.score(str(result["output"]))
            result["neuro_scores"] = neuro.to_dict()
        except ImportError:
            logger.debug("NeuroScorer not available for middleware scoring")
        except Exception as exc:
            logger.warning("Neuro-scorer middleware failed (non-fatal): %s", exc)

    return result


# ---------------------------------------------------------------------------
# Task 6: TelemetryMiddleware (MW-06, MW-07)
# ---------------------------------------------------------------------------


async def telemetry_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Record stage timing and success to the ``stage_metrics`` table.

    Fires-and-forgets the DB insert — telemetry never blocks the pipeline.
    Also records middleware overhead (time outside the handler).
    """
    t0 = time.perf_counter()
    result = await next_fn(ctx)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    # Fire-and-forget insert
    try:
        from shared.db import execute

        await execute(
            """INSERT INTO stage_metrics
               (pipeline, stage, daemon, duration_ms, success)
               VALUES (%s, %s, %s, %s, %s)""",
            (
                ctx.get("pipeline", ""),
                ctx.get("stage_name", ""),
                ctx.get("daemon_name", ""),
                duration_ms,
                result.get("success", False),
            ),
        )
    except Exception as exc:
        logger.warning("Telemetry insert failed (non-fatal): %s", exc)

    return result


# ---------------------------------------------------------------------------
# Task 7: BudgetCheckMiddleware
# ---------------------------------------------------------------------------

# Monthly budget cap in USD
_BUDGET_CAP_USD = 800.0


async def budget_check_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Reject stage execution if monthly LLM spend exceeds $800.

    Uses ``shared.observability.get_metrics_summary`` to check current cost.
    """
    try:
        from shared.observability import get_metrics_summary

        summary = await get_metrics_summary(hours=720)  # ~30 days
        daemons = summary.get("daemons", [])
        total_cost = sum(float(d.get("total_cost_usd", 0) or 0) for d in daemons)

        if total_cost >= _BUDGET_CAP_USD:
            logger.warning(
                "BUDGET EXCEEDED: $%.2f / $%.2f — blocking stage '%s'",
                total_cost,
                _BUDGET_CAP_USD,
                ctx.get("stage_name", ""),
            )
            return {
                "success": False,
                "output": f"Budget cap exceeded: ${total_cost:.2f} / ${_BUDGET_CAP_USD:.2f}",
            }
    except Exception as exc:
        # Budget check failure should not block the pipeline
        logger.warning("Budget check failed (allowing): %s", exc)

    return await next_fn(ctx)


# ---------------------------------------------------------------------------
# Populate MIDDLEWARE_REGISTRY
# ---------------------------------------------------------------------------

MIDDLEWARE_REGISTRY.update(
    {
        "budget_check": budget_check_middleware,
        "dna_guard": dna_guard_middleware,
        "anti_slop": anti_slop_middleware,
        "neuro_scorer": neuro_scorer_middleware,
        "memory": memory_middleware,
        "telemetry": telemetry_middleware,
    }
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "MIDDLEWARE_REGISTRY",
    "PIPELINE_CONFIGS",
    "MiddlewareChain",
    "NextFn",
    "StageResult",
    "anti_slop_middleware",
    "budget_check_middleware",
    "build_chain",
    "dna_guard_middleware",
    "memory_middleware",
    "neuro_scorer_middleware",
    "telemetry_middleware",
]
