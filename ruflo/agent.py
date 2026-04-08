"""
Ruflo Agent — Self-editing engineering agent for autonomous code improvement.

Papers: SICA (17-53% on SWE-Bench by self-editing own code),
AI Scientist v2 (paper passed ICLR workshop peer review),
AlphaEvolve (improved Gemini's own training code),
OpenEvolve (open AlphaEvolve on single RTX 4090),
SEA-TS (40% MAE reduction, discovers novel architectures),
LLM NAS w/ Feedback Memory (ρ=0.75 improvement on single GPU),
Continually Self-Improving AI (bootstrap → algorithm search).

Workflow:
1. Detect bottleneck from pipeline metrics + MAGMA causal chains
2. Propose code change (generate diff)
3. Shadow-test (run make test in subprocess)
4. If pass → commit. If fail → record in feedback memory to avoid re-proposing.

Gated behind RUFLO_ENABLED=1 (default 0).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("perseus.ruflo")

def _ruflo_enabled() -> bool:
    from shared.config import config
    return config.ruflo.enabled


def _ruflo_auto_apply() -> bool:
    from shared.config import config
    return config.ruflo.auto_apply_fixes


async def dispatch(task: dict) -> dict:
    """Route a Ruflo task to the appropriate handler.

    Task types: code_fix, code_review, code_refactor, security_scan,
    dependency_audit, implement_tool, test_generate.
    """
    if not _ruflo_enabled():
        return {"status": "disabled", "message": "RUFLO_ENABLED=0"}

    task_type = task.get("task_type", "")
    payload = task.get("payload", {})

    handlers = {
        "code_fix": _handle_code_fix,
        "code_review": _handle_code_review,
        "code_refactor": _handle_code_refactor,
        "security_scan": _handle_security_scan,
        "dependency_audit": _handle_dependency_audit,
        "implement_tool": _handle_implement_tool,
        "test_generate": _handle_test_generate,
    }

    handler = handlers.get(task_type)
    if not handler:
        return {"status": "error", "message": f"Unknown task type: {task_type}"}

    return await handler(payload)


async def propose_change(bottleneck: dict) -> dict | None:
    """SICA-style: analyze bottleneck and generate a code change proposal.

    Returns: {"files": [str], "diff": str, "rationale": str, "estimated_impact": float}
    or None if no viable change found.
    """
    if not _ruflo_enabled():
        return None

    description = bottleneck.get("description", "")
    file_hint = bottleneck.get("file", "")
    error_context = bottleneck.get("error", "")

    try:
        from shared.llm_client import llm
        prompt = (
            f"You are a senior Python engineer analyzing a production system bottleneck.\n\n"
            f"BOTTLENECK: {description}\n"
            f"FILE: {file_hint}\n"
            f"ERROR CONTEXT: {error_context[:500]}\n\n"
            f"Propose a minimal, safe code change to fix this.\n"
            f"Return JSON: {{\n"
            f'  "files": ["file/path.py"],\n'
            f'  "diff": "--- a/file\\n+++ b/file\\n@@ ... @@\\n-old line\\n+new line",\n'
            f'  "rationale": "why this fixes the issue",\n'
            f'  "estimated_impact": 0.0-1.0\n'
            f"}}"
        )

        result = await llm.generate(prompt, model="smart", temperature=0.2, operation="fix_proposal", daemon_name="ruflo")
        start = result.find("{")
        end = result.rfind("}") + 1
        proposal = json.loads(result[start:end])

        # Check feedback memory — avoid re-proposing failed changes
        known_failures = await feedback_lookup(
            proposal.get("files", [""])[0],
            "code_fix",
        )
        for failure in known_failures:
            if failure.get("diff", "")[:100] == proposal.get("diff", "")[:100]:
                logger.info(f"Ruflo: skipping proposal — previously failed on {failure.get('file')}")
                return None

        return proposal

    except Exception as e:
        logger.debug(f"Ruflo propose_change failed: {e}")
        return None


async def shadow_test(proposal: dict) -> dict:
    """Run tests on a proposed change in an isolated git worktree.

    Creates a throwaway worktree, applies the diff, runs pytest, then
    tears down the worktree. The live working tree is never modified.
    Returns: {"passed": bool, "output": str, "duration_s": float}
    """
    if not proposal or not proposal.get("diff"):
        return {"passed": False, "output": "no diff provided", "duration_s": 0}

    project_root = _get_project_root()
    start = datetime.now()

    worktree_dir = None
    try:
        # 1. Create an isolated git worktree so we never touch the live tree
        import tempfile
        worktree_dir = tempfile.mkdtemp(prefix="ruflo_shadow_")
        branch_name = f"ruflo-shadow-{int(start.timestamp())}"

        wt_add = subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, worktree_dir, "HEAD"],
            cwd=project_root,
            capture_output=True, text=True, timeout=30,
        )
        if wt_add.returncode != 0:
            return {"passed": False, "output": f"worktree setup failed: {wt_add.stderr[-300:]}", "duration_s": 0}

        # 1b. Overlay dirty working-tree state onto the worktree.
        # Without this, the shadow-test runs against clean HEAD and can
        # pass even when the fix will conflict with uncommitted changes
        # in the real tree.
        #
        # Order matters: the worktree starts at HEAD.
        #   1. git diff --cached (HEAD→index) brings it to the staged state.
        #   2. git diff (index→working-tree) brings it to the live state.
        # Reversing this produces wrong content for files with both
        # staged and unstaged edits.
        #
        # FAIL-CLOSED: if any overlay step fails, abort rather than
        # testing against a wrong tree state.
        for overlay_cmd in [["git", "diff", "--cached"], ["git", "diff"]]:
            try:
                dirty = subprocess.run(
                    overlay_cmd,
                    cwd=project_root,
                    capture_output=True, text=True, timeout=15,
                )
                if dirty.returncode == 0 and dirty.stdout.strip():
                    overlay_apply = subprocess.run(
                        ["git", "apply", "--allow-empty", "-"],
                        input=dirty.stdout,
                        cwd=worktree_dir,
                        capture_output=True, text=True, timeout=15,
                    )
                    if overlay_apply.returncode != 0:
                        return {
                            "passed": False,
                            "output": f"dirty-tree overlay failed to apply: {overlay_apply.stderr[-300:]}",
                            "duration_s": 0,
                        }
            except Exception as e:
                return {"passed": False, "output": f"dirty-tree overlay error: {e}", "duration_s": 0}

        # 1c. Copy untracked files into the worktree.
        # git diff does not cover brand-new files that haven't been
        # staged. Without this, the shadow worktree is missing files
        # that exist in the live tree and the proposed fix may interact with.
        try:
            ls_untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=project_root,
                capture_output=True, text=True, timeout=15,
            )
            if ls_untracked.returncode == 0 and ls_untracked.stdout.strip():
                import shutil
                for rel_path in ls_untracked.stdout.strip().split("\n"):
                    rel_path = rel_path.strip()
                    if not rel_path:
                        continue
                    src = Path(project_root) / rel_path
                    dst = Path(worktree_dir) / rel_path
                    if src.is_file():
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(dst))
        except Exception as e:
            return {"passed": False, "output": f"untracked file overlay error: {e}", "duration_s": 0}

        # 2. Apply the proposed diff inside the worktree
        apply = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=proposal.get("diff", ""),
            cwd=worktree_dir,
            capture_output=True, text=True, timeout=15,
        )
        if apply.returncode != 0:
            return {"passed": False, "output": f"diff does not apply cleanly: {apply.stderr[-300:]}", "duration_s": 0}

        subprocess.run(
            ["git", "apply", "-"],
            input=proposal.get("diff", ""),
            cwd=worktree_dir,
            capture_output=True, text=True, timeout=15,
        )

        # 3. Run tests against the patched worktree
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "-x", "--tb=short", "-q"],
            cwd=worktree_dir,
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "PYTHONPATH": worktree_dir},
        )

        duration = (datetime.now() - start).total_seconds()
        passed = result.returncode == 0
        output = result.stdout[-500:] if result.stdout else result.stderr[-500:]

        return {
            "passed": passed,
            "output": output,
            "duration_s": round(duration, 1),
        }

    except subprocess.TimeoutExpired:
        return {"passed": False, "output": "test timeout (120s)", "duration_s": 120}
    except Exception as e:
        return {"passed": False, "output": str(e), "duration_s": 0}
    finally:
        # 4. Always clean up the worktree
        if worktree_dir:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_dir],
                cwd=project_root,
                capture_output=True, timeout=15,
            )


async def feedback_lookup(file_path: str, change_type: str) -> list[dict]:
    """Query feedback memory for previous attempts at similar changes.

    LLM NAS w/ Feedback Memory: ρ=0.75 improvement correlation.
    Prevents re-proposing changes that already failed.
    """
    try:
        from shared.db import fetch_all
        rows = await fetch_all(
            """SELECT file_path, change_type, diff_preview, success, error_output, created_at
               FROM ruflo_patterns
               WHERE file_path = %s AND change_type = %s AND success = FALSE
               ORDER BY created_at DESC LIMIT 10""",
            (file_path, change_type),
        )
        return [dict(r) for r in rows] if rows else []
    except Exception:
        return []


async def record_outcome(
    task_id: int | None,
    file_path: str,
    change_type: str,
    diff: str,
    success: bool,
    error_output: str = "",
) -> None:
    """Record change outcome in feedback memory for future avoidance."""
    try:
        from shared.db import execute
        await execute(
            """INSERT INTO ruflo_patterns (file_path, change_type, diff_preview, success, error_output)
               VALUES (%s, %s, %s, %s, %s)""",
            (file_path, change_type, diff[:500], success, error_output[:500]),
        )
    except Exception as e:
        logger.debug(f"Ruflo record_outcome failed: {e}")


# ── Task Handlers ────────────────────────────────────────────────────

def _get_project_root() -> str:
    try:
        from shared.config import config
        return str(config.root_dir)
    except Exception:
        return os.getcwd()


async def _handle_code_fix(payload: dict) -> dict:
    """Fix a specific code issue."""
    proposal = await propose_change(payload)
    if not proposal:
        return {"status": "no_viable_fix", "message": "Could not generate a safe fix"}

    test_result = await shadow_test(proposal)

    if test_result["passed"] and _ruflo_auto_apply():
        # Apply the tested diff to the real working tree
        project_root = _get_project_root()
        apply_result = subprocess.run(
            ["git", "apply", "-"],
            input=proposal.get("diff", ""),
            cwd=project_root,
            capture_output=True, text=True, timeout=15,
        )
        if apply_result.returncode != 0:
            await record_outcome(None, proposal["files"][0], "code_fix", proposal["diff"], False, apply_result.stderr[:500])
            return {"status": "apply_failed", "proposal": proposal, "tests": test_result, "error": apply_result.stderr[:300]}
        await record_outcome(None, proposal["files"][0], "code_fix", proposal["diff"], True)
        return {"status": "applied", "proposal": proposal, "tests": test_result}
    elif test_result["passed"]:
        await record_outcome(None, proposal["files"][0], "code_fix", proposal["diff"], True)
        return {"status": "proposed", "proposal": proposal, "tests": test_result, "message": "RUFLO_AUTO_APPLY=0, needs manual review"}
    else:
        await record_outcome(None, proposal.get("files", ["unknown"])[0], "code_fix", proposal.get("diff", ""), False, test_result["output"])
        return {"status": "failed", "proposal": proposal, "tests": test_result}


async def _handle_code_review(payload: dict) -> dict:
    """Review code for issues."""
    try:
        from shared.llm_client import llm
        _file_path = payload.get("file", "")
        result = await llm.generate(
            f"Review this code for bugs, security issues, and performance problems:\n{payload.get('code', '')[:3000]}",
            model="smart",
        )
        return {"status": "reviewed", "findings": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _handle_code_refactor(payload: dict) -> dict:
    return await _handle_code_fix({**payload, "description": f"Refactor: {payload.get('description', '')}"})


async def _handle_security_scan(payload: dict) -> dict:
    try:
        from shared.llm_client import llm
        result = await llm.generate(
            f"Security audit this code. Flag: injection, XSS, auth bypass, info leak, insecure defaults.\n{payload.get('code', '')[:3000]}",
            model="smart",
        )
        return {"status": "scanned", "findings": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _handle_dependency_audit(payload: dict) -> dict:
    try:
        result = subprocess.run(
            ["python3", "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True, text=True, timeout=30,
        )
        return {"status": "audited", "outdated": json.loads(result.stdout) if result.stdout else []}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def _handle_implement_tool(payload: dict) -> dict:
    return await _handle_code_fix({**payload, "description": f"Implement: {payload.get('description', '')}"})


async def _handle_test_generate(payload: dict) -> dict:
    try:
        from shared.llm_client import llm
        result = await llm.generate(
            f"Generate pytest tests for this code:\n{payload.get('code', '')[:3000]}",
            model="smart",
        )
        return {"status": "generated", "tests": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
