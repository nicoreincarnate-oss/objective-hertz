"""
GSD + Ralph Loop execution engine for Perseus.

Any agent can use this to:
1. Break a complex task into structured subtasks (GSD)
2. Execute them sequentially with validation after each step (Ralph Loop)
3. Retry failed steps with error context injected back into the prompt
4. Loop until all checks pass or max retries exhausted

Used by:
- ClawdBot: skill creation, site building, browser tasks
- Titan: email composition with QA, proposal generation
- Sleep cycle: applying backprop changes with verification
- Any future agent that needs iterative refinement
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from shared.comms import record_decision
from shared.llm_client import llm

logger = logging.getLogger("perseus.execution_loop")

# Defaults
DEFAULT_MAX_RETRIES = 3
DEFAULT_MODEL = "smart"


@dataclass
class Step:
    """A single step in a GSD task plan."""
    name: str
    prompt: str
    check: Callable[[str], Awaitable[dict]] | None = None  # async fn(result) -> {"passed": bool, "error": "..."}
    model: str = DEFAULT_MODEL
    max_tokens: int = 4000
    temperature: float = 0.5
    result: str = ""
    passed: bool = False
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class TaskPlan:
    """A structured task plan (GSD framework)."""
    name: str
    description: str
    steps: list[Step]
    context: dict = field(default_factory=dict)
    agent: str = ""
    max_retries: int = DEFAULT_MAX_RETRIES


async def decompose_task(
    task_description: str,
    *,
    context: dict | None = None,
    model: str = "smart",
    max_steps: int = 7,
) -> list[dict]:
    """GSD Phase: Break a complex task into structured subtasks.

    Returns a list of step dicts: [{"name": "...", "prompt": "...", "check_description": "..."}]
    """
    prompt = f"""Break this task into {max_steps} or fewer sequential subtasks.
Each subtask should be independently executable and verifiable.

TASK: {task_description}

CONTEXT: {json.dumps(context or {})}

For each subtask, provide:
- name: short identifier (snake_case)
- prompt: the exact prompt to send to an LLM to execute this step
- check_description: how to verify this step succeeded (what to look for in the output)

Return JSON:
{{"steps": [
  {{"name": "...", "prompt": "...", "check_description": "..."}}
]}}"""

    result = await llm.generate(prompt, model=model, temperature=0.3, max_tokens=2000)

    try:
        start = result.find("{")
        end = result.rfind("}") + 1
        data = json.loads(result[start:end])
        return data.get("steps", [])[:max_steps]
    except (json.JSONDecodeError, ValueError):
        # Fallback: single step with the full task
        return [{"name": "full_task", "prompt": task_description, "check_description": "task completed"}]


async def execute_plan(plan: TaskPlan) -> dict[str, Any]:
    """Ralph Loop: execute a task plan step by step with retry on failure.

    For each step:
    1. Execute the prompt
    2. Run the check function (if provided)
    3. If check fails, re-inject error context and retry
    4. Move to next step only when current passes (or max retries hit)

    Returns: {"completed": bool, "steps_passed": N, "steps_total": N, "results": [...]}
    """
    results: list[dict[str, Any]] = []
    all_passed = True

    for _i, step in enumerate(plan.steps):
        step_result = await _execute_step(step, plan, previous_results=results)
        results.append(step_result)

        if not step_result["passed"]:
            all_passed = False
            logger.warning(
                f"Plan '{plan.name}' step '{step.name}' failed after {step.attempts} attempts: "
                f"{step_result.get('last_error', 'unknown')[:100]}"
            )
            # Don't continue if a step fails — later steps depend on earlier ones
            break

        logger.debug(f"Plan '{plan.name}' step '{step.name}' passed (attempt {step.attempts})")

    # Record the execution
    try:
        await record_decision(
            agent=plan.agent or "execution_loop",
            decision_type="plan_execution",
            context={
                "plan": plan.name,
                "description": plan.description[:200],
                "steps_total": len(plan.steps),
            },
            decision={
                "completed": all_passed,
                "steps_passed": sum(1 for r in results if r["passed"]),
                "steps_total": len(plan.steps),
            },
            reasoning=f"{'All steps passed' if all_passed else f'Failed at step {len(results)}'}",
        )
    except Exception:
        pass

    return {
        "completed": all_passed,
        "steps_passed": sum(1 for r in results if r["passed"]),
        "steps_total": len(plan.steps),
        "results": results,
    }


async def _execute_step(
    step: Step,
    plan: TaskPlan,
    previous_results: list[dict],
) -> dict[str, Any]:
    """Execute a single step with retry loop (Ralph Loop pattern)."""
    # Build context from previous step results
    prev_context = ""
    if previous_results:
        prev_summaries = []
        for r in previous_results[-3:]:  # Last 3 results for context
            prev_summaries.append(f"Step '{r['name']}': {r['result'][:500]}")
        prev_context = "\n\nPREVIOUS STEPS:\n" + "\n---\n".join(prev_summaries)

    for attempt in range(1, plan.max_retries + 1):
        step.attempts = attempt

        # Build prompt with error context from previous attempts
        error_context = ""
        if step.errors:
            error_context = (
                f"\n\nPREVIOUS ATTEMPT FAILED. Error: {step.errors[-1]}\n"
                f"Fix the issue and try again. Do NOT repeat the same mistake."
            )

        full_prompt = f"{step.prompt}{prev_context}{error_context}"

        # Execute
        result = await llm.generate(
            full_prompt,
            model=step.model,
            max_tokens=step.max_tokens,
            temperature=step.temperature,
            pipeline_stage=f"loop:{plan.name}:{step.name}",
        )
        step.result = result

        # Check
        if step.check:
            try:
                check_result = await step.check(result)
                if check_result.get("passed"):
                    step.passed = True
                    return {
                        "name": step.name,
                        "passed": True,
                        "result": result[:2000],
                        "attempts": attempt,
                    }
                else:
                    error = check_result.get("error", "check failed")
                    step.errors.append(error)
                    logger.debug(f"Step '{step.name}' check failed (attempt {attempt}): {error[:100]}")
            except Exception as e:
                step.errors.append(str(e))
                logger.debug(f"Step '{step.name}' check threw exception (attempt {attempt}): {e}")
        else:
            # No check function — pass if we got a non-empty result
            if result and len(result.strip()) > 10:
                step.passed = True
                return {
                    "name": step.name,
                    "passed": True,
                    "result": result[:2000],
                    "attempts": attempt,
                }
            step.errors.append("Empty or too-short result")

    # All retries exhausted
    return {
        "name": step.name,
        "passed": False,
        "result": step.result[:2000] if step.result else "",
        "attempts": step.attempts,
        "last_error": step.errors[-1] if step.errors else "max retries",
    }


# ── Convenience builders ──────────────────────────────────────────

def build_plan(
    name: str,
    description: str,
    steps: list[dict],
    *,
    agent: str = "",
    context: dict | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    default_model: str = DEFAULT_MODEL,
) -> TaskPlan:
    """Build a TaskPlan from a list of step dicts (from decompose_task or manual)."""
    plan_steps: list[Step] = []
    for s in steps:
        plan_steps.append(Step(
            name=s.get("name", f"step_{len(plan_steps)}"),
            prompt=s.get("prompt", ""),
            model=s.get("model", default_model),
            max_tokens=s.get("max_tokens", 4000),
            temperature=s.get("temperature", 0.5),
        ))
    return TaskPlan(
        name=name,
        description=description,
        steps=plan_steps,
        context=context or {},
        agent=agent,
        max_retries=max_retries,
    )


async def gsd_and_loop(
    task_description: str,
    *,
    agent: str = "",
    context: dict | None = None,
    checks: dict[str, Callable] | None = None,
    model: str = "smart",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """One-call convenience: decompose a task (GSD) then execute it (Ralph Loop).

    Args:
        task_description: What needs to be done
        agent: Which agent is running this
        context: Additional context
        checks: Map of step_name → async check function
        model: LLM model for decomposition and execution
        max_retries: Per-step retry limit

    Returns: execution result dict
    """
    # GSD: decompose
    step_dicts = await decompose_task(task_description, context=context, model=model)

    # Build plan with optional checks
    plan_steps: list[Step] = []
    for s in step_dicts:
        step = Step(
            name=s.get("name", f"step_{len(plan_steps)}"),
            prompt=s.get("prompt", ""),
            model=model,
        )
        if checks and s.get("name") in checks:
            step.check = checks[s["name"]]
        plan_steps.append(step)

    plan = TaskPlan(
        name="gsd_task",
        description=task_description[:200],
        steps=plan_steps,
        context=context or {},
        agent=agent,
        max_retries=max_retries,
    )

    # Ralph Loop: execute
    return await execute_plan(plan)
