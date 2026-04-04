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
from datetime import UTC, date, datetime
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
    """Check if an env-based feature flag is active.

    Defaults to TRUE for security-critical flags — explicitly set to "false" to disable.
    """
    return os.environ.get(name, "true").lower() in ("true", "1", "yes")


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
    "forbidden_tokens",
]

PIPELINE_CONFIGS: dict[str, list[str]] = {
    "titan": list(TITAN_MIDDLEWARE),
    "clawdbot": ["budget_check", "dna_guard", "anti_slop", "neuro_scorer", "telemetry", "forbidden_tokens"],
    "hermes": ["budget_check", "dna_guard", "neuro_scorer", "telemetry", "forbidden_tokens"],
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

    if not _flag("ENABLE_MIDDLEWARE"):
        logger.warning(
            "ENABLE_MIDDLEWARE is OFF — all middleware protections are disabled. "
            "Set ENABLE_MIDDLEWARE=true to activate budget checks, credential scanning, "
            "and telemetry."
        )
        return MiddlewareChain()  # empty chain — passes through directly

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

# Monthly budget cap — read from config, fallback to $800
_ALERT_THRESHOLD = 0.8  # Downgrade Haiku at 80%


async def _get_budget_cap() -> float:
    """Read budget cap from DB config (operator-settable), falling back to env config then $800."""
    try:
        from shared.db import get_config
        db_cap = await get_config("monthly_budget_cap", None)
        if db_cap is not None:
            return float(db_cap)
        from shared.config import config
        return float(config.budget.monthly_cap)
    except Exception:
        return 800.0


async def budget_check_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Pre-execution budget gate with cost estimation.

    1. Estimate cost of upcoming task from historical cost_events averages
    2. Evaluate all applicable budget policies (company + agent + stage)
    3. If estimated cost would exceed remaining budget: reject, fall back to Ollama
    4. AEGIS: DB errors -> REJECT and fall back to Ollama (fail CLOSED)

    Feature flag: PRE_EXECUTION_BUDGET_GATE_ENABLED (default false)
    When disabled, falls back to legacy post-hoc budget check.

    When ``ENABLE_CONSOLIDATED_BUDGET`` is ON, this middleware also handles
    per-LLM-call model downgrade decisions (via ``requested_model`` in ctx).
    Fails CLOSED on DB error when consolidated flag is ON.
    """
    cap = await _get_budget_cap()
    stage_name = ctx.get("stage_name", "")
    daemon_name = ctx.get("daemon_name", "")

    pre_gate_enabled = os.environ.get(
        "PRE_EXECUTION_BUDGET_GATE_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    if pre_gate_enabled:
        try:
            from shared.cost_events import get_average_cost_by_task_type
            from tools.budget_guard import evaluate_policies

            # Step 1: Estimate cost of this task
            estimated_cost = await get_average_cost_by_task_type(stage_name)
            if estimated_cost is None:
                # No historical data — use conservative default ($0.05 per stage)
                estimated_cost = 0.05

            # Step 2: Evaluate all applicable policies
            decision = await evaluate_policies(
                agent_id=daemon_name,
                pipeline_stage=stage_name,
            )

            # Step 3: Check if estimated cost fits within remaining budget
            if not decision.allowed:
                logger.warning(
                    "BUDGET GATE BLOCKED: %s — %s (estimated $%.4f)",
                    stage_name, decision.reason, estimated_cost,
                )
                ctx["budget_fallback_to_ollama"] = True
                return {
                    "success": False,
                    "output": f"Budget gate: {decision.reason}",
                    "budget_blocked": True,
                    "fallback": "ollama",
                }

            if decision.most_restrictive:
                remaining = decision.most_restrictive.remaining_usd
                if estimated_cost > remaining:
                    logger.warning(
                        "BUDGET GATE: estimated $%.4f > remaining $%.2f for %s/%s — rejecting",
                        estimated_cost, remaining,
                        decision.most_restrictive.scope_type,
                        decision.most_restrictive.scope_value,
                    )
                    ctx["budget_fallback_to_ollama"] = True
                    return {
                        "success": False,
                        "output": f"Budget gate: estimated ${estimated_cost:.4f} exceeds remaining ${remaining:.2f}",
                        "budget_blocked": True,
                        "fallback": "ollama",
                    }

            # Attach budget context for downstream use
            ctx["budget_remaining"] = decision.most_restrictive.remaining_usd if decision.most_restrictive else None
            ctx["budget_warnings"] = decision.warnings

        except Exception as exc:
            # AEGIS: Fail CLOSED — DB errors reject and fallback to Ollama
            logger.error(
                "BUDGET GATE DB ERROR — failing CLOSED (rejecting stage '%s'): %s",
                stage_name, exc,
            )
            ctx["budget_fallback_to_ollama"] = True
            return {
                "success": False,
                "output": f"Budget gate: DB error — fail closed ({exc})",
                "budget_blocked": True,
                "fallback": "ollama",
            }

        return await next_fn(ctx)

    # --- Consolidated path (ENABLE_CONSOLIDATED_BUDGET) ---
    if os.environ.get("ENABLE_CONSOLIDATED_BUDGET", "").lower() in ("true", "1", "yes"):
        try:
            from shared.db import fetch_val

            month = date.today().replace(day=1)
            total = await fetch_val(
                "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
                (month,),
            ) or 0

            percent_used = float(total) / cap if cap > 0 else 1.0

            # Per-LLM-call check (injected by LLMClient.generate)
            if "requested_model" in ctx:
                requested_model = ctx["requested_model"]
                if percent_used >= 1.0:
                    logger.warning(
                        "Budget exceeded ($%.2f/$%.2f) — forcing Ollama",
                        float(total), cap,
                    )
                    ctx["resolved_model"] = "local"
                elif percent_used >= _ALERT_THRESHOLD and requested_model == "fast":
                    logger.info(
                        "Budget at %.0f%% — downgrading fast to Ollama",
                        percent_used * 100,
                    )
                    ctx["resolved_model"] = "local"
                else:
                    ctx["resolved_model"] = requested_model
                return await next_fn(ctx)

            # Pipeline-stage check (no requested_model)
            if float(total) >= cap:
                logger.warning(
                    "BUDGET EXCEEDED: $%.2f / $%.2f — blocking stage '%s'",
                    float(total), cap, stage_name,
                )
                return {
                    "success": False,
                    "output": f"Budget cap exceeded: ${float(total):.2f} / ${cap:.2f}",
                }

        except Exception as exc:
            logger.error("Budget check DB failed — REJECTING call (fail-closed): %s", exc)
            if "requested_model" in ctx:
                ctx["resolved_model"] = "local"  # Force Ollama
                return await next_fn(ctx)
            return {"success": False, "output": f"Budget check unavailable: {exc}"}

        return await next_fn(ctx)

    # --- Legacy path (flag OFF): observability-based check ---
    # AEGIS FIX: Change from fail-open to fail-closed
    try:
        from shared.observability import get_metrics_summary

        summary = await get_metrics_summary(hours=720)  # ~30 days
        daemons = summary.get("daemons", [])
        total_cost = sum(float(d.get("total_cost_usd", 0) or 0) for d in daemons)

        if total_cost >= cap:
            logger.warning(
                "BUDGET EXCEEDED: $%.2f / $%.2f — blocking stage '%s'",
                total_cost,
                cap,
                stage_name,
            )
            return {
                "success": False,
                "output": f"Budget cap exceeded: ${total_cost:.2f} / ${cap:.2f}",
            }
    except Exception as exc:
        # AEGIS FIX: Fail CLOSED — reject and fallback to Ollama
        logger.error(
            "BUDGET CHECK DB ERROR — failing CLOSED (was: failing open): %s", exc,
        )
        return {
            "success": False,
            "output": f"Budget check: DB error — fail closed ({exc})",
            "budget_blocked": True,
            "fallback": "ollama",
        }

    return await next_fn(ctx)


# ---------------------------------------------------------------------------
# Standalone budget check for LLM calls (consolidated authority)
# ---------------------------------------------------------------------------


async def check_budget_for_llm_call(requested_model: str) -> str:
    """Standalone budget check for LLM calls (used by LLMClient when consolidated flag is ON).

    Returns the resolved model — either the requested model or "local" for Ollama fallback.
    Fails CLOSED: DB errors return "local".
    """
    # NOTE: ENABLE_CONSOLIDATED_BUDGET flag check removed — function always executes.
    # The flag is deprecated; this is now the sole budget authority.

    try:
        from shared.db import fetch_val

        month = date.today().replace(day=1)
        total = await fetch_val(
            "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
            (month,),
        ) or 0

        cap = await _get_budget_cap()
        percent_used = float(total) / cap if cap > 0 else 1.0

        if percent_used >= 1.0:
            logger.warning("Budget exceeded ($%.2f/$%.2f) — forcing Ollama", float(total), cap)
            return "local"

        if percent_used >= _ALERT_THRESHOLD and requested_model == "fast":
            logger.info("Budget at %.0f%% — downgrading fast to Ollama", percent_used * 100)
            return "local"

        return requested_model

    except Exception as exc:
        logger.error("Budget DB failed — fail-closed, forcing Ollama: %s", exc)
        return "local"


# ---------------------------------------------------------------------------
# Forbidden Token Scanner Middleware (Phase 12: FP-04)
# ---------------------------------------------------------------------------


async def forbidden_token_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Scan stage output for leaked secrets and forbidden tokens.

    Feature-flag gated by FORBIDDEN_TOKEN_SCAN_ENABLED.
    Runs AFTER the stage (output sanitizer) — checks result["output"].
    """
    if not _flag("FORBIDDEN_TOKEN_SCAN_ENABLED"):
        return await next_fn(ctx)

    result = await next_fn(ctx)

    output_text = str(result.get("output", ""))
    if not output_text:
        return result

    try:
        from openjarvis.security.forbidden_tokens import scan

        hits = scan(output_text)
        if hits:
            names = ", ".join(sorted({h.pattern_name for h in hits}))
            logger.error(
                "FORBIDDEN TOKENS in stage '%s': %s (%d matches)",
                ctx.get("stage_name", "unknown"),
                names,
                len(hits),
            )
            result["forbidden_token_violations"] = [
                {"pattern": h.pattern_name, "position": h.position} for h in hits
            ]
            # Redact the output using credential stripper as fallback
            from openjarvis.security.credential_stripper import CredentialStripper

            result["output"] = CredentialStripper().strip(output_text)
    except Exception as exc:
        logger.warning("Forbidden token scan failed (non-fatal): %s", exc)

    return result


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
        "forbidden_tokens": forbidden_token_middleware,
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
    "check_budget_for_llm_call",
    "dna_guard_middleware",
    "forbidden_token_middleware",
    "memory_middleware",
    "neuro_scorer_middleware",
    "telemetry_middleware",
]
