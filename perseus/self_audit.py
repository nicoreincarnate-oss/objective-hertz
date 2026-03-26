"""Self-Audit — Autonomous codebase health with multi-agent consensus.

Every 3 hours: git diff → Claude analysis → A2A discussion → consensus vote → apply.
The system audits itself, knowing it IS the system. Agents discuss findings and
vote on whether to apply fixes. Only consensus-approved changes get applied.
"""

import json
import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any

from shared.comms import call_agent_capability, record_decision
from shared.config import config
from shared.db import emit_event, get_config, set_config
from shared.llm_client import llm

logger = logging.getLogger("perseus.self_audit")

# The 12 AI-coder failure modes identified in the original audit
FAILURE_MODES = [
    "dead_code",
    "copy_paste_divergence",
    "column_order_bugs",
    "hollow_stubs",
    "json_parsing_fragility",
    "silent_swallowing",
    "sync_async_confusion",
    "schema_mismatch",
    "missing_error_handling",
    "type_mismatches",
    "wrong_defaults",
    "disconnected_stores",
]

# Files that self-audit can NEVER modify (superset of backprop immutables)
AUDIT_IMMUTABLE = {
    "titan/compliance.py",
    "shared/db.py",
    "shared/config.py",
    "scripts/init-db.sql",
    "perseus/self_audit.py",
    "perseus/backprop.py",
    "clawdbot/safety.py",
    "orchestrator.py",
}

MAX_FILES_PER_AUDIT = 15
MAX_FINDINGS_PER_CYCLE = 10
CONSENSUS_THRESHOLD = 2  # 2 of 3 agents must approve
AGENTS_TO_CONSULT = ["titan", "clawdbot", "hermes"]


def _get_root() -> Path:
    try:
        from shared.config import config
        return Path(config.root_dir)
    except Exception:
        return Path(__file__).resolve().parent.parent


# ── Core ──────────────────────────────────────────────────────────────


async def run_self_audit() -> dict[str, Any]:
    """Run one self-audit cycle: scope → analyze → discuss → vote → apply."""
    cycle_id = f"audit-{int(time.time())}"
    root = _get_root()

    try:
        # Phase 1: Scope
        changed = await _get_changed_files(root)
        if not changed:
            # No git changes — do a random scan of 5 files
            changed = _get_random_scan_files(root, count=5)

        if not changed:
            logger.info("Self-audit: nothing to analyze")
            return {"total_findings": 0, "approved": 0, "applied": 0}

        changed = changed[:MAX_FILES_PER_AUDIT]
        logger.info("Self-audit: analyzing %d files", len(changed))

        # Phase 2: Analyze
        all_findings: list[dict] = []
        for filepath in changed:
            try:
                full_path = root / filepath
                if not full_path.exists() or not full_path.suffix == ".py":
                    continue
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 20000:
                    content = content[:20000] + "\n# ... (truncated for analysis)"
                findings = await _analyze_file(filepath, content)
                all_findings.extend(findings)
            except Exception as e:
                logger.debug("Failed to analyze %s: %s", filepath, e)

        if not all_findings:
            logger.info("Self-audit: no issues found")
            await set_config("last_audit_time", time.time())
            return {"total_findings": 0, "approved": 0, "applied": 0}

        # Deduplicate and prioritize
        all_findings = _deduplicate(all_findings)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        all_findings.sort(key=lambda f: severity_order.get(f.get("severity", "low"), 3))
        all_findings = all_findings[:MAX_FINDINGS_PER_CYCLE]

        logger.info("Self-audit: %d findings to discuss", len(all_findings))

        # Phase 3: A2A Discussion
        discussed = await _discuss_with_agents(all_findings)

        # Phase 4: Consensus
        approved = [f for f in discussed if f.get("_consensus") == "approved"]
        rejected = [f for f in discussed if f.get("_consensus") == "rejected"]
        deferred = [f for f in discussed if f.get("_consensus") == "deferred"]

        logger.info("Self-audit consensus: %d approved, %d rejected, %d deferred",
                     len(approved), len(rejected), len(deferred))

        # Phase 5: Apply
        apply_counts = await _apply_approved_fixes(approved, cycle_id, root)

        # Phase 6: Report
        summary = {
            "cycle_id": cycle_id,
            "files_analyzed": len(changed),
            "total_findings": len(all_findings),
            "approved": len(approved),
            "rejected": len(rejected),
            "deferred": len(deferred),
            **apply_counts,
            "findings": [
                {"file": f.get("file"), "issue": f.get("issue"), "severity": f.get("severity"),
                 "consensus": f.get("_consensus")}
                for f in discussed
            ],
        }

        await emit_event("self_audit_completed", summary)
        await record_decision(
            agent="self_audit",
            decision_type="audit_cycle",
            context={"files": changed, "cycle_id": cycle_id},
            decision=summary,
            reasoning=f"Analyzed {len(changed)} files, found {len(all_findings)} issues, applied {apply_counts.get('applied', 0)} fixes",
        )
        await set_config("last_audit_time", time.time())
        await set_config("last_audit_commit", _get_head_commit(_get_root()))

        return summary

    except Exception as e:
        logger.warning("Self-audit cycle failed: %s", e)
        await emit_event("self_audit_failed", {"error": str(e)[:300], "cycle_id": cycle_id})
        return {"total_findings": 0, "approved": 0, "applied": 0, "error": str(e)[:300]}


# ── Scoping ───────────────────────────────────────────────────────────


def _get_head_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


async def _get_changed_files(root: Path) -> list[str]:
    """Get Python files changed since last audit.

    Checks four sources and unions them:
    1. Committed changes since last audit (last_audit_commit..HEAD)
    2. Unstaged working-tree modifications (git diff --name-only)
    3. Staged but uncommitted changes (git diff --cached --name-only)
    4. Untracked files (git ls-files --others --exclude-standard)

    Without (2)-(4), dirty or new files are invisible to self-audit
    and the cycle silently falls back to a random scan.
    """
    all_files: set[str] = set()
    last_commit = await get_config("last_audit_commit", "") or ""

    # 1. Committed changes since last audit
    if last_commit:
        committed_cmd = ["git", "diff", "--name-only", last_commit, "HEAD"]
    else:
        committed_cmd = ["git", "diff", "--name-only", "HEAD~10", "HEAD"]

    # 2. Unstaged working-tree changes
    unstaged_cmd = ["git", "diff", "--name-only"]

    # 3. Staged (cached) changes
    staged_cmd = ["git", "diff", "--cached", "--name-only"]

    # 4. Untracked files (brand-new .py files not yet added to git)
    untracked_cmd = ["git", "ls-files", "--others", "--exclude-standard"]

    for cmd in [committed_cmd, unstaged_cmd, staged_cmd, untracked_cmd]:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                for f in result.stdout.strip().split("\n"):
                    f = f.strip()
                    if f and f.endswith(".py"):
                        all_files.add(f)
        except Exception as e:
            logger.debug("git diff failed (%s): %s", cmd[:4], e)

    return sorted(all_files)


def _get_random_scan_files(root: Path, count: int = 5) -> list[str]:
    """Pick random Python files for periodic scan when no git changes."""
    py_files = []
    for dirpath in ["perseus", "titan", "hermes", "clawdbot", "shared", "tools"]:
        full = root / dirpath
        if full.is_dir():
            for f in full.rglob("*.py"):
                rel = str(f.relative_to(root))
                if rel not in AUDIT_IMMUTABLE and "__pycache__" not in rel:
                    py_files.append(rel)

    if not py_files:
        return []
    return random.sample(py_files, min(count, len(py_files)))


# ── Analysis ──────────────────────────────────────────────────────────


async def _analyze_file(filepath: str, content: str) -> list[dict[str, Any]]:
    """Analyze one file for AI-coder failure modes."""
    is_immutable = filepath in AUDIT_IMMUTABLE
    immutable_note = "\nNOTE: This file is IMMUTABLE — report issues but do NOT propose code edits." if is_immutable else ""

    prompt = f"""You are auditing code that was partially written by AI. You are part of this system — this code runs YOU. Be precise, not paranoid.

File: {filepath}
{immutable_note}

```python
{content}
```

Check for these specific failure modes: {json.dumps(FAILURE_MODES)}

For each REAL issue found, return JSON:
{{
  "findings": [
    {{
      "file": "{filepath}",
      "line": 0,
      "issue": "short description",
      "failure_mode": "one of: {', '.join(FAILURE_MODES)}",
      "severity": "low|medium|high|critical",
      "proposed_fix": "exact code change description, or empty if immutable",
      "fix_type": "code_edit|config_change|rule_change|no_fix_needed",
      "reasoning": "why this matters for the running system"
    }}
  ]
}}

Rules:
- Only report REAL issues with concrete evidence from the code above.
- "Could be improved" is NOT a finding — only actual bugs, risks, or broken behavior.
- If the code is fine, return {{"findings": []}}
- Severity: critical = money/security, high = breaks functionality, medium = degrades reliability, low = code quality.
- For immutable files: set fix_type to "no_fix_needed" and explain the issue in reasoning.

Return ONLY JSON."""

    try:
        result = await llm.generate(
            prompt, model="smart", max_tokens=1500, temperature=0.1,
            pipeline_stage="self_audit",
        )
        start = result.find("{")
        end = result.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(result[start:end])
            findings = parsed.get("findings", [])
            # Tag immutable files
            if is_immutable:
                for f in findings:
                    f["fix_type"] = "no_fix_needed"
                    f["_immutable"] = True
            return findings
    except Exception as e:
        logger.debug("Analysis failed for %s: %s", filepath, e)

    return []


def _deduplicate(findings: list[dict]) -> list[dict]:
    """Remove duplicate findings by file+issue."""
    seen: set[str] = set()
    unique = []
    for f in findings:
        key = f"{f.get('file', '')}:{f.get('issue', '')}"
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


# ── Multi-Agent Discussion ────────────────────────────────────────────


async def _discuss_with_agents(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send each finding + actual source code to each agent for review.

    Each agent receives the full code snippet surrounding the issue so it
    can verify the bug is real and the proposed fix is safe — not just
    rubber-stamp a text summary.
    """
    root = _get_root()

    for finding in findings:
        votes: dict[str, str] = {}
        reasons: dict[str, str] = {}

        # Read the actual code the agents will review
        code_snippet = _read_code_for_finding(root, finding)

        for agent_name in AGENTS_TO_CONSULT:
            try:
                response = await call_agent_capability(
                    agent_name,
                    "review_finding",
                    {"finding": finding, "code_snippet": code_snippet},
                    timeout=30,
                )
                vote = _parse_vote(response)
                votes[agent_name] = vote
                reasons[agent_name] = (response or {}).get("reason", "")[:300]
            except Exception as e:
                logger.debug("Failed to get review from %s: %s", agent_name, e)
                votes[agent_name] = "defer"
                reasons[agent_name] = str(e)[:200]

        # Determine consensus
        approve_count = sum(1 for v in votes.values() if v == "approve")
        reject_count = sum(1 for v in votes.values() if v == "reject")

        if approve_count >= CONSENSUS_THRESHOLD:
            consensus = "approved"
        elif reject_count >= CONSENSUS_THRESHOLD:
            consensus = "rejected"
        else:
            consensus = "deferred"

        finding["_votes"] = votes
        finding["_vote_reasons"] = reasons
        finding["_consensus"] = consensus

        logger.info("Finding [%s] %s: votes=%s → %s",
                     finding.get("severity"), finding.get("issue", "")[:60],
                     votes, consensus)

    return findings


def _read_code_for_finding(root: Path, finding: dict) -> str:
    """Read the actual source code surrounding a finding.

    Returns a window of code centered on the reported line, or the full
    file (truncated) if no line is specified.  This is what agents will
    review — they should never vote without seeing the code.
    """
    filepath = finding.get("file", "")
    if not filepath:
        return ""

    try:
        full_path = root / filepath
        if not full_path.exists():
            return f"# FILE NOT FOUND: {filepath}"

        lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        line_num = finding.get("line", 0)

        if line_num and line_num > 0:
            # Show 30 lines of context around the reported line
            start = max(0, line_num - 15)
            end = min(len(lines), line_num + 15)
            numbered = [f"{i+1:4d} | {lines[i]}" for i in range(start, end)]
            return f"# {filepath} (lines {start+1}-{end})\n" + "\n".join(numbered)
        else:
            # No line specified — return first 150 lines
            limit = min(len(lines), 150)
            numbered = [f"{i+1:4d} | {lines[i]}" for i in range(limit)]
            if len(lines) > limit:
                numbered.append(f"# ... ({len(lines) - limit} more lines)")
            return f"# {filepath}\n" + "\n".join(numbered)

    except Exception as e:
        return f"# Error reading {filepath}: {e}"


def _parse_vote(response: dict[str, Any] | None) -> str:
    """Extract approve/reject/defer from an agent's A2A response."""
    if not response:
        return "defer"

    # Check for structured vote field (from review_finding capability)
    if isinstance(response.get("vote"), str):
        v = response["vote"].lower().strip()
        if v in ("approve", "reject", "defer"):
            return v

    # Parse from text answer (legacy fallback)
    text = str(response.get("answer", response.get("result", response.get("text", "")))).lower()

    if "reject" in text or "veto" in text or "no, " in text or "should not" in text:
        return "reject"
    if "approve" in text or "yes" in text or "fix it" in text or "agree" in text:
        return "approve"
    return "defer"


# ── Apply Fixes ───────────────────────────────────────────────────────


async def _apply_approved_fixes(
    approved: list[dict[str, Any]],
    cycle_id: str,
    root: Path | None = None,
) -> dict[str, int]:
    """Apply consensus-approved fixes. Returns counts."""
    applied = 0
    skipped = 0
    modified_files: list[str] = []  # Track exactly which files we changed
    if root is None:
        root = _get_root()

    for finding in approved:
        fix_type = finding.get("fix_type", "no_fix_needed")
        filepath = finding.get("file", "")

        if fix_type == "no_fix_needed" or finding.get("_immutable"):
            skipped += 1
            continue

        if fix_type == "config_change":
            try:
                from perseus.backprop import apply_config_change
                key = finding.get("config_key", filepath)
                value = finding.get("proposed_value", finding.get("proposed_fix", ""))
                reason = f"Self-audit {cycle_id}: {finding.get('issue', '')}"
                success = await apply_config_change(key, value, reason, cycle_id)
                if success:
                    applied += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.debug("Config change failed: %s", e)
                skipped += 1

        elif fix_type == "rule_change":
            try:
                from perseus.backprop import apply_rule_change
                rule_id = finding.get("rule_id", 0)
                active = finding.get("proposed_active", True)
                reason = f"Self-audit {cycle_id}: {finding.get('issue', '')}"
                success = await apply_rule_change(rule_id, active, reason, cycle_id)
                if success:
                    applied += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.debug("Rule change failed: %s", e)
                skipped += 1

        elif fix_type == "code_edit":
            if filepath in AUDIT_IMMUTABLE:
                logger.warning("Self-audit tried to edit immutable file %s — blocked", filepath)
                skipped += 1
                continue

            # Try Ruflo multi-agent swarm first if enabled
            ruflo_dispatched = False
            if config.ruflo.enabled:
                try:
                    from shared.comms import request_task_result
                    ruflo_result = await request_task_result("code_fix", {
                        "capability": "code_fix",
                        "finding": finding,
                        "file": filepath,
                        "cycle_id": cycle_id,
                        "source": "self_audit",
                        "source_id": f"audit-{cycle_id}-{filepath}",
                    }, timeout_seconds=120)
                    ruflo_status = (ruflo_result or {}).get("status", "error")
                    if ruflo_status == "applied":
                        ruflo_dispatched = True
                        applied += 1
                        # Ruflo proposals can touch multiple files.
                        # Stage all of them, not just the original finding path,
                        # otherwise the commit omits Ruflo-modified companion files.
                        ruflo_files = (ruflo_result or {}).get("proposal", {}).get("files", [])
                        if ruflo_files:
                            modified_files.extend(ruflo_files)
                        else:
                            modified_files.append(filepath)
                        logger.info("Ruflo applied fix for %s", filepath)
                    elif ruflo_status == "proposed":
                        ruflo_dispatched = True
                        # Fix was proposed but not auto-applied — don't count as applied
                        logger.info("Ruflo proposed fix for %s (needs manual review)", filepath)
                    elif ruflo_status in ("failed", "apply_failed", "no_viable_fix"):
                        logger.warning("Ruflo failed to fix %s: %s", filepath, ruflo_status)
                        # Fall through to inline fix
                    else:
                        logger.warning("Ruflo returned unexpected status for %s: %s", filepath, ruflo_status)
                except Exception as e:
                    logger.debug("Ruflo dispatch failed for %s, falling back to inline: %s", filepath, e)

            # Fallback to inline single-pass fix
            if not ruflo_dispatched:
                try:
                    success = await _safe_code_edit(
                        root, filepath,
                        finding.get("proposed_fix", ""),
                        f"Self-audit {cycle_id}: {finding.get('issue', '')}",
                    )
                    if success:
                        applied += 1
                        modified_files.append(filepath)
                    else:
                        skipped += 1
                except Exception as e:
                    logger.debug("Code edit failed for %s: %s", filepath, e)
                    skipped += 1
        else:
            skipped += 1

    # Git commit only the specific files we actually modified.
    # Never use `git add -A` — that vacuums unrelated local changes
    # (config edits, in-progress work) into the self-audit commit.
    if modified_files:
        try:
            # Stage only the files self-audit touched
            subprocess.run(
                ["git", "add", "--"] + modified_files,
                capture_output=True, text=True, cwd=str(root), timeout=10,
            )
            # Verify something is actually staged (guard against no-op edits)
            diff_check = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                capture_output=True, cwd=str(root), timeout=10,
            )
            if diff_check.returncode != 0:  # returncode 1 = there are staged changes
                msg = f"self-audit {cycle_id}: {applied} fix(es) in {', '.join(modified_files)}"
                subprocess.run(
                    ["git", "commit", "-m", msg],
                    capture_output=True, text=True, cwd=str(root), timeout=10,
                )
                logger.info("Self-audit committed %d fixes: %s", applied, modified_files)
            else:
                logger.debug("Self-audit: nothing staged after adding %s", modified_files)
        except Exception as e:
            logger.debug("Git commit failed: %s", e)

    return {"applied": applied, "skipped": skipped}


async def _safe_code_edit(root: Path, filepath: str, fix_description: str, reason: str) -> bool:
    """Apply a code edit described in natural language.

    For safety, we use the LLM to generate the exact old_text/new_text replacement,
    then validate it before applying.
    """
    if filepath in AUDIT_IMMUTABLE:
        return False

    full_path = root / filepath
    if not full_path.exists():
        return False

    content = full_path.read_text(encoding="utf-8")

    # Ask LLM for the exact replacement
    prompt = f"""Given this file and fix description, provide the EXACT text replacement.

File: {filepath}
Fix: {fix_description}

Current file content:
```python
{content[:8000]}
```

Return JSON only:
{{"old_text": "exact text to find in the file", "new_text": "exact replacement text"}}

The old_text must be an EXACT substring of the current file. Keep changes minimal."""

    try:
        result = await llm.generate(prompt, model="fast", max_tokens=1000, temperature=0.0)
        start = result.find("{")
        end = result.rfind("}") + 1
        if start < 0 or end <= start:
            return False

        parsed = json.loads(result[start:end])
        old_text = parsed.get("old_text", "")
        new_text = parsed.get("new_text", "")

        if not old_text or not new_text or old_text == new_text:
            return False

        if old_text not in content:
            logger.debug("old_text not found in %s — skipping edit", filepath)
            return False

        # Apply the replacement
        new_content = content.replace(old_text, new_text, 1)
        full_path.write_text(new_content, encoding="utf-8")

        # Validate: syntax check the written file. If it fails, restore
        # the original content and return False. Without this gate the
        # inline fallback can land broken code while claiming success.
        try:
            import ast
            ast.parse(new_content, filename=filepath)
        except SyntaxError as se:
            logger.warning("Inline edit produced invalid syntax in %s (line %s) — rolling back", filepath, se.lineno)
            full_path.write_text(content, encoding="utf-8")
            return False

        # Quick smoke-test: run pytest on just this file's tests (if any).
        # Timeout is short; failure OR inability to run means rollback.
        # FAIL-CLOSED: if validation infra is unavailable (timeout, crash),
        # we roll back rather than landing an unverified edit.
        test_path = root / "tests" / f"test_{Path(filepath).stem}.py"
        if test_path.exists():
            try:
                tr = subprocess.run(
                    ["python3", "-m", "pytest", str(test_path), "-x", "--tb=line", "-q"],
                    capture_output=True, text=True, cwd=str(root), timeout=30,
                    env={**os.environ, "PYTHONPATH": str(root)},
                )
                if tr.returncode != 0:
                    logger.warning("Inline edit failed tests for %s — rolling back", filepath)
                    full_path.write_text(content, encoding="utf-8")
                    return False
            except subprocess.TimeoutExpired:
                logger.warning("Inline edit test timed out for %s — rolling back (fail-closed)", filepath)
                full_path.write_text(content, encoding="utf-8")
                return False
            except Exception as te:
                logger.warning("Inline edit test errored for %s: %s — rolling back (fail-closed)", filepath, te)
                full_path.write_text(content, encoding="utf-8")
                return False

        logger.info("Applied code edit to %s: %s", filepath, reason[:80])
        return True

    except Exception as e:
        logger.debug("Code edit generation failed for %s: %s", filepath, e)
        return False
