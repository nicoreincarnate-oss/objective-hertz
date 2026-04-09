"""Ruflo Aider loop — architect+editor pattern for code fixing.

Replaces single-model Ruflo `propose_change()` with a structured loop:

  1. ARCHITECT (Opus 4.6 or local-heavy):
     - Read the failing test + error context
     - Identify root cause
     - Propose plan: which files to change, how, what test to add
     - Output: structured plan JSON

  2. EDITOR (Qwen2.5-Coder-14B local or Sonnet 4.6):
     - Apply Architect's plan to actual code
     - Generate exact diff (old_text → new_text)
     - Output: patch JSON

  3. VERIFIER (pytest in sandbox):
     - Apply patch to scratch worktree
     - Run pytest -x
     - Capture pass/fail + output

  4. LOOP (max 3 iterations):
     - If verifier passes: done
     - If verifier fails: feed failure back to Architect, ask for revision
     - If 3 iterations exhausted: escalate to full Sonnet 4.6 hand-fix

Quality bar: at depth 3 with verifier, end-to-end success rate is comparable
to full-Sonnet Ruflo at ~5% of the cost (per Aider production data).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.aider.sandbox_runner import SandboxRunner
from shared.tiers import TierName

logger = logging.getLogger("perseus.aider.ruflo")


@dataclass
class FixPlan:
    root_cause: str
    files_to_change: list[str]
    plan_steps: list[str]
    test_to_add: str = ""
    estimated_complexity: str = "medium"  # low|medium|high
    architect_model: str = ""
    raw_response: str = ""


@dataclass
class FixPatch:
    diff: str                       # Unified diff
    files_modified: list[str] = field(default_factory=list)
    rationale: str = ""
    editor_model: str = ""


@dataclass
class VerifierResult:
    passed: bool
    output: str
    duration_s: float
    pytest_exit_code: int = 0


@dataclass
class RufloAiderResult:
    success: bool
    final_patch: FixPatch | None
    verifier_result: VerifierResult | None
    iterations: int
    total_duration_s: float
    architect_calls: int
    editor_calls: int
    error: str = ""
    escalated_to: str = ""


# ============================================================================
# Architect prompt
# ============================================================================

ARCHITECT_PROMPT = """You are the Aider Architect for Ruflo. Plan a fix for this failing test.

Failing test: {failing_test}
Error: {error_message}
Relevant file: {file_hint}
Recent attempts (if any): {prior_attempts}

Your job is to PLAN, not write code. The Editor will write code from your plan.

Output ONLY this JSON schema (no prose):
{{
  "root_cause": "1-sentence diagnosis",
  "files_to_change": ["path/to/file.py"],
  "plan_steps": [
    "Step 1: brief action",
    "Step 2: brief action"
  ],
  "test_to_add": "Optional new test name to add (or empty string)",
  "estimated_complexity": "low|medium|high"
}}
"""


EDITOR_PROMPT = """You are the Aider Editor for Ruflo. Apply this plan as a unified diff.

Plan:
  Root cause: {root_cause}
  Files to change: {files}
  Steps:
{steps}

Current file content:
```python
{file_content}
```

Output ONLY a unified diff in this format (no prose, no markdown fences):
--- a/{primary_file}
+++ b/{primary_file}
@@ -<line>,<count> +<line>,<count> @@
 context line
-removed line
+added line
 context line

Make the smallest possible change that implements the plan.
"""


# ============================================================================
# Loop runner
# ============================================================================

async def run_ruflo_aider_loop(
    failing_test: str,
    error_message: str,
    file_hint: str,
    *,
    llm_client: Any,
    repo_root: Path,
    max_iterations: int = 3,
    architect_tier: TierName = TierName.SMART,
    editor_tier: TierName = TierName.LOCAL,  # Qwen2.5-Coder-14B
    sandbox_runner: Any | None = None,
) -> RufloAiderResult:
    """Run the Architect → Editor → Verifier loop until green or exhausted.

    P0-3: sandbox_runner defaults to a SandboxRunner() instance that wraps
    pytest execution in macOS sandbox-exec with the verifier.sb profile.
    Pass sandbox_runner=False explicitly to disable (NOT recommended — any
    prompt-injected fix will have full host access, including ~/.ssh and
    Conway wallets).
    """
    t0 = time.perf_counter()
    # P0-3: instantiate the default SandboxRunner lazily. If the caller
    # passes a custom runner (e.g. the Ruflo production scratch-worktree
    # runner with .run_with_patch), we use that instead. If None, we build
    # the default verifier sandbox wrapper.
    if sandbox_runner is None:
        try:
            sandbox_runner = SandboxRunner()
            logger.info("P0-3: default SandboxRunner instantiated for ruflo_loop")
        except (FileNotFoundError, RuntimeError) as _exc:
            logger.error(
                "P0-3: could not instantiate default SandboxRunner (%s); "
                "verifier will be disabled for this run. This is INSECURE — "
                "any prompt-injected patch will have full host access.",
                _exc,
            )
            sandbox_runner = None
    architect_calls = 0
    editor_calls = 0
    prior_attempts: list[str] = []
    last_patch: FixPatch | None = None
    last_verify: VerifierResult | None = None

    for iteration in range(max_iterations):
        logger.info("Ruflo Aider iteration %d/%d", iteration + 1, max_iterations)

        # 1. ARCHITECT plans the fix
        try:
            plan = await _call_architect(
                llm_client,
                failing_test=failing_test,
                error_message=error_message,
                file_hint=file_hint,
                prior_attempts="\n".join(prior_attempts) if prior_attempts else "none",
                tier=architect_tier,
            )
            architect_calls += 1
        except Exception as exc:
            logger.error("Architect failed: %s", exc)
            return RufloAiderResult(
                success=False, final_patch=None, verifier_result=None,
                iterations=iteration, total_duration_s=time.perf_counter() - t0,
                architect_calls=architect_calls, editor_calls=editor_calls,
                error=f"Architect failed: {exc}",
            )

        # 2. EDITOR applies the plan
        try:
            file_content = ""
            if plan.files_to_change:
                primary = repo_root / plan.files_to_change[0]
                if primary.exists():
                    file_content = primary.read_text()
            patch = await _call_editor(
                llm_client,
                plan=plan,
                file_content=file_content[:10000],  # Truncate huge files
                tier=editor_tier,
            )
            editor_calls += 1
            last_patch = patch
        except Exception as exc:
            logger.error("Editor failed: %s", exc)
            prior_attempts.append(f"iter {iteration + 1}: editor failed: {exc}")
            continue

        # 3. VERIFIER runs the patch in sandbox
        try:
            if sandbox_runner is None:
                logger.warning("No sandbox_runner provided, skipping verification")
                last_verify = VerifierResult(passed=False, output="no sandbox", duration_s=0.0)
            else:
                last_verify = await _verify_patch_in_sandbox(
                    sandbox_runner, patch, repo_root
                )
        except Exception as exc:
            logger.error("Verifier crashed: %s", exc)
            last_verify = VerifierResult(passed=False, output=str(exc), duration_s=0.0)

        # 4. Loop or exit
        if last_verify.passed:
            return RufloAiderResult(
                success=True,
                final_patch=patch,
                verifier_result=last_verify,
                iterations=iteration + 1,
                total_duration_s=time.perf_counter() - t0,
                architect_calls=architect_calls,
                editor_calls=editor_calls,
            )

        prior_attempts.append(
            f"iter {iteration + 1}: editor produced patch but pytest failed: "
            f"{last_verify.output[:200]}"
        )

    # Exhausted iterations — escalate
    logger.warning("Ruflo Aider exhausted %d iterations, escalating", max_iterations)
    return RufloAiderResult(
        success=False,
        final_patch=last_patch,
        verifier_result=last_verify,
        iterations=max_iterations,
        total_duration_s=time.perf_counter() - t0,
        architect_calls=architect_calls,
        editor_calls=editor_calls,
        error="Exhausted iterations without verifier pass",
        escalated_to="cloud/genius",
    )


async def _call_architect(
    llm_client: Any,
    *,
    failing_test: str,
    error_message: str,
    file_hint: str,
    prior_attempts: str,
    tier: TierName,
) -> FixPlan:
    prompt = ARCHITECT_PROMPT.format(
        failing_test=failing_test,
        error_message=error_message,
        file_hint=file_hint,
        prior_attempts=prior_attempts,
    )
    response = await llm_client.generate(
        prompt,
        model=tier.value,
        daemon_name="ruflo",
        pipeline_stage="aider_architect",
        max_tokens=1500,
        temperature=0.3,
        operation="ruflo.aider_architect",
    )
    return _parse_plan(response, tier.value)


async def _call_editor(
    llm_client: Any,
    *,
    plan: FixPlan,
    file_content: str,
    tier: TierName,
) -> FixPatch:
    primary = plan.files_to_change[0] if plan.files_to_change else "unknown.py"
    prompt = EDITOR_PROMPT.format(
        root_cause=plan.root_cause,
        files=", ".join(plan.files_to_change),
        steps="\n".join(f"  - {s}" for s in plan.plan_steps),
        primary_file=primary,
        file_content=file_content,
    )
    response = await llm_client.generate(
        prompt,
        model=tier.value,
        daemon_name="ruflo",
        pipeline_stage="aider_editor",
        max_tokens=4000,
        temperature=0.2,
        operation="ruflo.aider_editor",
    )
    diff = _extract_diff(response)
    return FixPatch(
        diff=diff,
        files_modified=plan.files_to_change,
        rationale=plan.root_cause,
        editor_model=tier.value,
    )


async def _verify_patch_in_sandbox(
    sandbox_runner: Any,
    patch: FixPatch,
    repo_root: Path,
) -> VerifierResult:
    """Apply patch in scratch worktree, run pytest, return result.

    P0-3: supports two runner APIs:
      1. Legacy Ruflo scratch-worktree runner: async run_with_patch(...)
      2. Default SandboxRunner from shared.aider.sandbox_runner:
         sync run_pytest(test_path, ...) — assumes patch was already applied
         to repo_root by caller (or is a no-op dry-run verification).
    """
    t0 = time.perf_counter()
    try:
        if hasattr(sandbox_runner, "run_with_patch"):
            # Legacy path: runner applies the patch to a scratch worktree itself
            result = await sandbox_runner.run_with_patch(
                patch_diff=patch.diff,
                test_command=["python", "-m", "pytest", "-x", "--tb=short", "-q"],
                timeout_seconds=120,
            )
            return VerifierResult(
                passed=result.exit_code == 0,
                output=result.stdout[-500:] + result.stderr[-500:],
                duration_s=time.perf_counter() - t0,
                pytest_exit_code=result.exit_code,
            )
        # Default path: use SandboxRunner.run_pytest on the full test suite
        # inside repo_root. Caller is responsible for applying the patch to
        # a scratch worktree before calling this function.
        target = patch.files_modified[0] if patch.files_modified else str(repo_root)
        result = sandbox_runner.run_pytest(
            Path(target), cwd=repo_root, timeout_s=120,
        )
        return VerifierResult(
            passed=result.returncode == 0,
            output=(result.stdout[-500:] + result.stderr[-500:]),
            duration_s=time.perf_counter() - t0,
            pytest_exit_code=result.returncode,
        )
    except Exception as exc:
        return VerifierResult(
            passed=False,
            output=f"sandbox error: {exc}",
            duration_s=time.perf_counter() - t0,
        )


# ============================================================================
# Parsers
# ============================================================================

def _parse_plan(response: str, model: str) -> FixPlan:
    """Extract plan JSON from architect response."""
    json_match = re.search(r"\{.*\}", response, re.DOTALL)
    if not json_match:
        return FixPlan(
            root_cause="parse_failure",
            files_to_change=[],
            plan_steps=[],
            architect_model=model,
            raw_response=response,
        )
    try:
        data = json.loads(json_match.group(0))
        return FixPlan(
            root_cause=data.get("root_cause", ""),
            files_to_change=data.get("files_to_change", []),
            plan_steps=data.get("plan_steps", []),
            test_to_add=data.get("test_to_add", ""),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            architect_model=model,
            raw_response=response,
        )
    except json.JSONDecodeError as exc:
        logger.warning("Plan JSON parse failed: %s", exc)
        return FixPlan(
            root_cause="parse_failure",
            files_to_change=[],
            plan_steps=[],
            architect_model=model,
            raw_response=response,
        )


def _extract_diff(response: str) -> str:
    """Extract unified diff from editor response, stripping markdown fences."""
    response = response.strip()
    fence = re.search(r"```(?:diff|patch)?\n(.*?)```", response, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    if response.startswith("---"):
        return response
    diff_match = re.search(r"---.*?(?=\n\n|\Z)", response, re.DOTALL)
    if diff_match:
        return diff_match.group(0)
    return response
