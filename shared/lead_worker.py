"""Lead/Worker agent loop — Phase 43.

The Aider-style architect+editor pattern, generalized.

For long-running agent loops, split responsibilities:
  Lead   = strong model (Sonnet/Opus) — plans steps, reviews progress
  Worker = cheap model (Haiku/local)  — executes each step

Cost savings from Goose production data: 40-60% on agent loops vs single-model.

Quality preservation: Lead reviews progress every N steps and re-plans if needed.
Worker is constrained by Lead's plan, so it can't go off the rails.

This module is the GENERALIZATION. Daemon-specific Aider loops live in
shared/aider/ruflo_loop.py and shared/aider/clawdbot_loop.py.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from shared.tiers import TierName

logger = logging.getLogger("perseus.lead_worker")


@dataclass
class Step:
    """One step in a Lead's plan."""
    id: str
    description: str
    tools_needed: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""


@dataclass
class StepResult:
    step_id: str
    success: bool
    output: str
    tool_calls: list[dict] = field(default_factory=list)
    error: str = ""
    duration_ms: int = 0
    cost_usd: float = 0.0


@dataclass
class LeadWorkerConfig:
    lead_tier: TierName = TierName.SMART
    worker_tier: TierName = TierName.FAST
    max_steps: int = 20
    plan_revision_interval: int = 5
    fail_fast: bool = False
    revisable: bool = True
    architect_revisits_on_failure: bool = True


@dataclass
class LeadWorkerResult:
    success: bool
    final_output: str
    steps_executed: int
    steps_total: int
    plan_revisions: int
    total_cost_usd: float
    total_duration_ms: int
    step_results: list[StepResult] = field(default_factory=list)
    error: str = ""


# Type aliases for callbacks the daemon must provide
PlanFn = Callable[[str, dict, list[StepResult]], Awaitable[list[Step]]]
ExecuteFn = Callable[[Step, dict], Awaitable[StepResult]]
ReviewFn = Callable[[list[Step], list[StepResult], dict], Awaitable[dict]]


class LeadWorkerLoop:
    """Lead/Worker agent loop. Daemon-agnostic — daemons provide plan/execute callbacks."""

    def __init__(
        self,
        config: LeadWorkerConfig,
        plan_fn: PlanFn,
        execute_fn: ExecuteFn,
        review_fn: ReviewFn | None = None,
    ):
        self.config = config
        self.plan_fn = plan_fn
        self.execute_fn = execute_fn
        self.review_fn = review_fn

    async def run(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> LeadWorkerResult:
        """Execute a goal using the lead/worker split.

        1. Lead creates step-by-step plan
        2. For each step:
           a. Worker executes step
           b. Result accumulates in context
           c. Every plan_revision_interval steps, Lead reviews progress
           d. Lead optionally revises plan
        3. Returns final result with all step outputs
        """
        context = context or {}
        t0 = time.perf_counter()
        all_step_results: list[StepResult] = []
        plan_revisions = 0
        total_cost = 0.0

        # Step 1 — Lead creates initial plan
        logger.info("LeadWorker: planning goal %r with %s", goal[:80], self.config.lead_tier.value)
        try:
            # deepcopy: workers must not see each other's mutations — P1-7
            plan = await self.plan_fn(goal, copy.deepcopy(context), [])
        except Exception as exc:
            return LeadWorkerResult(
                success=False, final_output="", steps_executed=0,
                steps_total=0, plan_revisions=0, total_cost_usd=0.0,
                total_duration_ms=int((time.perf_counter() - t0) * 1000),
                error=f"Lead failed to plan: {exc}",
            )

        if not plan:
            return LeadWorkerResult(
                success=False, final_output="", steps_executed=0,
                steps_total=0, plan_revisions=0, total_cost_usd=0.0,
                total_duration_ms=int((time.perf_counter() - t0) * 1000),
                error="Lead returned empty plan",
            )

        plan = plan[: self.config.max_steps]

        # Step 2 — Worker executes each step
        i = 0
        while i < len(plan):
            step = plan[i]
            logger.debug("LeadWorker: executing step %s/%d: %s", step.id, len(plan), step.description[:60])

            try:
                # deepcopy: workers must not see each other's mutations — P1-7
                result = await self.execute_fn(step, copy.deepcopy(context))
            except Exception as exc:
                result = StepResult(
                    step_id=step.id,
                    success=False,
                    output="",
                    error=f"Worker exception: {exc}",
                )

            all_step_results.append(result)
            total_cost += result.cost_usd
            context[f"step_{step.id}_output"] = result.output

            if not result.success:
                if self.config.fail_fast:
                    break
                if self.config.architect_revisits_on_failure and self.config.revisable:
                    logger.info("LeadWorker: step %s failed, asking Lead to revise", step.id)
                    try:
                        # deepcopy: workers must not see each other's mutations — P1-7
                        new_plan = await self.plan_fn(goal, copy.deepcopy(context), all_step_results)
                        plan_revisions += 1
                        if new_plan:
                            plan = plan[: i + 1] + new_plan[: self.config.max_steps - i - 1]
                    except Exception as exc:
                        logger.warning("Lead revision failed: %s", exc)

            # Periodic review
            if (
                self.config.revisable
                and self.review_fn is not None
                and (i + 1) % self.config.plan_revision_interval == 0
                and i < len(plan) - 1
            ):
                try:
                    # deepcopy: workers must not see each other's mutations — P1-7
                    review = await self.review_fn(plan, all_step_results, copy.deepcopy(context))
                    if review.get("revise", False):
                        plan_revisions += 1
                        new_plan = await self.plan_fn(goal, copy.deepcopy(context), all_step_results)
                        if new_plan:
                            plan = plan[: i + 1] + new_plan[: self.config.max_steps - i - 1]
                except Exception as exc:
                    logger.debug("Periodic review failed: %s", exc)

            i += 1

        # Step 3 — Final summary
        successful_steps = [r for r in all_step_results if r.success]
        success = len(successful_steps) == len(all_step_results) and len(all_step_results) > 0
        final_output = (
            successful_steps[-1].output if successful_steps else ""
        )

        return LeadWorkerResult(
            success=success,
            final_output=final_output,
            steps_executed=len(all_step_results),
            steps_total=len(plan),
            plan_revisions=plan_revisions,
            total_cost_usd=total_cost,
            total_duration_ms=int((time.perf_counter() - t0) * 1000),
            step_results=all_step_results,
        )


__all__ = [
    "Step",
    "StepResult",
    "LeadWorkerConfig",
    "LeadWorkerResult",
    "LeadWorkerLoop",
]
