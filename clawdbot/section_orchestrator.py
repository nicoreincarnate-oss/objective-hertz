"""Parallel section orchestrator for ClawdBot v2.

Runs all section agents concurrently with semaphore-based throttling,
collects results, tracks costs, and emits progress events.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clawdbot.renderer import PlaywrightPool
    from clawdbot.section_planner import BuildPlan

from clawdbot.section_agent import SectionResult, generate_section

logger = logging.getLogger("perseus.clawdbot.section_orchestrator")

# Concurrency limits
_LLM_CONCURRENCY = 5
_VLM_CONCURRENCY = 2


# ---------------------------------------------------------------------------
# Cost tracking (Task 5)
# ---------------------------------------------------------------------------


@dataclass
class SectionCost:
    """Per-section cost breakdown."""

    section_type: str
    generation_tokens: int = 0
    generation_cost_usd: float = 0.0
    vlm_calls: int = 0
    vlm_cost_usd: float = 0.0
    render_time_s: float = 0.0
    total_time_s: float = 0.0
    iterations: int = 0


def estimate_section_cost(model: str, output_tokens: int) -> float:
    """Estimate cost for a section generation call.

    Model cost map (blended input+output per 1K tokens):
    - "fast" (Haiku): $0.001/1K
    - "smart" (Sonnet): $0.015/1K
    - "genius" (Opus): $0.075/1K
    - "ollama:*": $0.00
    """
    if model.startswith("ollama:"):
        return 0.0
    cost_per_1k: dict[str, float] = {
        "fast": 0.001,
        "smart": 0.015,
        "genius": 0.075,
    }
    rate = cost_per_1k.get(model, 0.015)
    return (output_tokens / 1000.0) * rate


# ---------------------------------------------------------------------------
# Build result
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    """Aggregated result of building all sections."""

    sections: dict[str, SectionResult] = field(default_factory=dict)
    section_costs: dict[str, SectionCost] = field(default_factory=dict)
    total_iterations: int = 0
    total_time_s: float = 0.0
    total_cost_usd: float = 0.0
    all_passed: bool = False
    failed_sections: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Progress emission
# ---------------------------------------------------------------------------


async def _emit_progress(
    section_type: str,
    status: str,
    iteration: int,
    score: float | None,
) -> None:
    """Emit progress event for monitoring and UI display.

    Status values: generating, rendering, scoring, iterating, passed, failed.
    Wraps DB call in try/except so missing DB does not block generation.
    """
    payload = {
        "section_type": section_type,
        "status": status,
        "iteration": iteration,
        "score": score,
    }
    try:
        from shared.db import emit_event
        await emit_event("section_progress", payload)
    except Exception:
        logger.debug("Progress event emission failed (DB unavailable): %s %s", section_type, status)


# ---------------------------------------------------------------------------
# Single-section wrapper (throttled)
# ---------------------------------------------------------------------------


async def _generate_one(
    plan_section: Any,
    contract: dict[str, Any],
    tokens: Any,
    pool: PlaywrightPool | None,
    tier: Any | None,
    llm_sem: asyncio.Semaphore,
    vlm_sem: asyncio.Semaphore,
) -> SectionResult:
    """Generate a single section with semaphore throttling."""
    section_type = plan_section.section_type

    await _emit_progress(section_type, "generating", 0, None)

    async with llm_sem:
        result = await generate_section(
            plan=plan_section,
            contract=contract,
            tokens=tokens,
            pool=pool,
            tier=tier,
        )

    status = "passed" if result.passed else "failed"
    score_val = result.score.overall if result.score is not None else None
    await _emit_progress(section_type, status, result.iterations, score_val)

    return result


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def build_all_sections(
    build_plan: BuildPlan,
    pool: PlaywrightPool | None = None,
) -> BuildResult:
    """Generate all sections in parallel with visual feedback.

    Uses asyncio.gather with semaphores to limit concurrent LLM and VLM calls.
    Pipeline: while Ollama scores section N, LLM generates section N+1.
    """
    t0 = time.monotonic()

    llm_sem = asyncio.Semaphore(_LLM_CONCURRENCY)
    vlm_sem = asyncio.Semaphore(_VLM_CONCURRENCY)

    contract = build_plan.design_contract
    tokens = build_plan.design_tokens
    tier = build_plan.tier

    tasks = []
    for sp in build_plan.sections:
        tasks.append(
            _generate_one(sp, contract, tokens, pool, tier, llm_sem, vlm_sem)
        )

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results
    sections: dict[str, SectionResult] = {}
    section_costs: dict[str, SectionCost] = {}
    total_iterations = 0
    total_cost = 0.0
    failed: list[str] = []

    for sp, raw in zip(build_plan.sections, raw_results, strict=False):
        if isinstance(raw, BaseException):
            logger.error("Section %s raised: %s", sp.section_type, raw)
            sections[sp.section_type] = SectionResult(
                section_type=sp.section_type,
                html="",
                screenshot=None,
                score=None,
                iterations=0,
                passed=False,
                error=str(raw),
            )
            failed.append(sp.section_type)
            continue

        result: SectionResult = raw
        sections[result.section_type] = result
        total_iterations += result.iterations
        total_cost += result.generation_cost_usd

        # Build cost record
        vlm_calls = result.iterations if pool is not None else 0
        section_costs[result.section_type] = SectionCost(
            section_type=result.section_type,
            generation_tokens=result.generation_tokens,
            generation_cost_usd=result.generation_cost_usd,
            vlm_calls=vlm_calls,
            vlm_cost_usd=0.0,  # Ollama VLM is free
            render_time_s=0.0,  # render time included in total
            total_time_s=result.total_time_s,
            iterations=result.iterations,
        )

        if not result.passed:
            failed.append(result.section_type)

    elapsed = time.monotonic() - t0

    return BuildResult(
        sections=sections,
        section_costs=section_costs,
        total_iterations=total_iterations,
        total_time_s=elapsed,
        total_cost_usd=total_cost,
        all_passed=len(failed) == 0,
        failed_sections=failed,
    )


# ---------------------------------------------------------------------------
# Cost logging
# ---------------------------------------------------------------------------


async def log_build_costs(result: BuildResult, lead_id: int) -> None:
    """Record build costs in the database for tracking.

    Wraps in try/except so missing DB does not block the build.
    """
    try:
        import json

        from shared.db import execute

        costs_payload = {
            st: {
                "tokens": sc.generation_tokens,
                "cost_usd": sc.generation_cost_usd,
                "vlm_calls": sc.vlm_calls,
                "iterations": sc.iterations,
                "time_s": sc.total_time_s,
            }
            for st, sc in result.section_costs.items()
        }

        await execute(
            """INSERT INTO site_build_metrics (lead_id, total_cost_usd, total_time_s,
               total_iterations, sections_passed, sections_failed, cost_breakdown)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                lead_id,
                result.total_cost_usd,
                result.total_time_s,
                result.total_iterations,
                len(result.sections) - len(result.failed_sections),
                len(result.failed_sections),
                json.dumps(costs_payload),
            ),
        )
    except Exception:
        logger.warning("Failed to log build costs for lead %d", lead_id, exc_info=True)
