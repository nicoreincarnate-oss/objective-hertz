"""Phase 3 Wave 2: Ruflo Aider bug-fix handler.

Wraps shared.aider.ruflo_loop.run_ruflo_aider_loop as an opt-in runtime
path for Ruflo's code-fixing workflow. Invoked from ruflo.agent.dispatch
when ENABLE_AIDER_LOOPS=true.

Uses the Architect→Editor→Verifier loop:
- Architect: Trinity-Large-Thinking (primary) or Sonnet 4.6 (fallback)
- Editor: Trinity Mini (primary) or Qwen3-30B-A3B (fallback)
- Verifier: pytest inside macOS sandbox-exec (P0-3 SandboxRunner default)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from shared.aider.ruflo_loop import run_ruflo_aider_loop, RufloAiderResult
from shared.tiers import TierName

logger = logging.getLogger("perseus.ruflo.aider")


def aider_loops_enabled() -> bool:
    """Runtime check — only route through Aider when operator opts in."""
    return os.environ.get("ENABLE_AIDER_LOOPS", "false").lower() == "true"


async def handle_aider_fix_task(
    task_payload: dict,
    *,
    llm_client: Any,
    repo_root: Path,
) -> dict:
    """Accept a Ruflo fix-task payload, route through the Aider loop.

    Returns a dict shaped like the existing ruflo.agent fix-task response:
      {
        "status": "complete|failed|escalated|skipped",
        "patch": {...},
        "iterations": int,
        "verifier_result": {...},
        "architect_model": str,
        "editor_model": str,
        "error": str | None,
      }
    """
    if not aider_loops_enabled():
        return {
            "status": "skipped",
            "error": "ENABLE_AIDER_LOOPS=false (Phase 3 opt-in flag)",
        }

    failing_test = task_payload.get("failing_test", "")
    error_message = task_payload.get("error_message", "")
    file_hint = task_payload.get("file_hint", "")

    if not failing_test or not error_message:
        return {
            "status": "failed",
            "error": "Missing failing_test or error_message in payload",
        }

    try:
        result: RufloAiderResult = await run_ruflo_aider_loop(
            failing_test=failing_test,
            error_message=error_message,
            file_hint=file_hint,
            llm_client=llm_client,
            repo_root=repo_root,
            max_iterations=3,
            architect_tier=TierName.GENIUS,  # Trinity-Large-Thinking via GENIUS tier
            editor_tier=TierName.LOCAL,      # Trinity Mini via LOCAL tier
        )
    except Exception as exc:
        logger.exception("Ruflo Aider loop crashed: %s", exc)
        return {
            "status": "failed",
            "error": f"Aider loop exception: {exc}",
        }

    return {
        "status": "complete" if result.success else "escalated",
        "patch": result.final_patch.__dict__ if result.final_patch else None,
        "iterations": result.iterations,
        "verifier_result": (
            result.verifier_result.__dict__ if result.verifier_result else None
        ),
        "architect_model": result.final_patch.editor_model if result.final_patch else "",
        "editor_model": result.final_patch.editor_model if result.final_patch else "",
        "error": result.error or None,
        "escalated_to": result.escalated_to,
    }
