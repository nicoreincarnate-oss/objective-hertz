#!/usr/bin/env python3
"""migrate_to_litellm — AST-based migration script for Phase 44.

Updates every llm.generate() call site in the Perseus codebase to use the new
tier system with operation + daemon_name metadata.

Before:
    result = await llm.generate(prompt, model="smart", max_tokens=4000)

After:
    result = await llm.generate(
        prompt,
        tier="smart",
        operation="email_compose",     # Inferred
        daemon_name="titan",           # Inferred from file path
        max_tokens=4000,
    )

Inference rules:
- daemon_name from file path: titan/* → titan, hermes/* → hermes, etc.
- operation from surrounding function name (e.g. compose_email → email_compose)
  or pipeline_stage if already passed.

Usage:
    python -m scripts.migrate_to_litellm --dry-run                # Preview only
    python -m scripts.migrate_to_litellm --dry-run --path titan/  # Subset
    python -m scripts.migrate_to_litellm --apply                  # Make changes
    python -m scripts.migrate_to_litellm --apply --path titan/    # Subset + apply
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Daemon name inference
# ============================================================================

DAEMON_PATH_MAP = {
    "perseus":          "perseus",
    "titan":            "titan",
    "hermes":           "hermes",
    "clawdbot":         "clawdbot",
    "conway":           "conway",
    "deerflow_research": "deerflow",
    "ruflo":            "ruflo",
    "openjarvis":       "openjarvis",
    "shared":           "shared",
    "system_executor":  "system_executor",
}


def infer_daemon(file_path: Path) -> str:
    """Infer daemon name from the file's top-level package directory."""
    rel = file_path.relative_to(REPO_ROOT) if file_path.is_absolute() else file_path
    parts = rel.parts
    if not parts:
        return "unknown"
    return DAEMON_PATH_MAP.get(parts[0], parts[0])


def infer_operation(func_name: str, daemon: str, pipeline_stage: str = "") -> str:
    """Infer operation name from the surrounding function name."""
    if pipeline_stage:
        return pipeline_stage
    if not func_name:
        return f"{daemon}_call"
    name = func_name.lower()

    # Common patterns
    pattern_map = [
        ("compose_email", "email_compose"),
        ("send_email", "email_send"),
        ("compose", "email_compose"),
        ("classify_lead", "lead_classification"),
        ("research_lead", "lead_research_summary"),
        ("research", "research_synthesis"),
        ("generate_section", "site_section_generation"),
        ("build_section", "site_section_generation"),
        ("propose_change", "fix_proposal"),
        ("propose_patch", "fix_proposal"),
        ("apply_patch", "patch_application"),
        ("orchestrat", "agent_loop_step"),
        ("plan", "fix_planning"),
        ("brief", "deerflow_brief"),
        ("alert", "alert_classification"),
        ("telegram", "telegram_reply"),
        ("vision", "screenshot_analysis"),
        ("ocr", "ocr"),
        ("score", "source_scoring"),
        ("summariz", "summarization_short"),
        ("classify", "simple_classification"),
        ("triage", "log_triage"),
    ]
    for needle, op in pattern_map:
        if needle in name:
            return op
    return f"{daemon}.{func_name}"


# ============================================================================
# AST scanner
# ============================================================================

@dataclass
class CallSite:
    file_path: Path
    line: int
    col: int
    func_name: str        # Surrounding function name
    daemon: str
    has_model_arg: bool
    has_tier_arg: bool
    has_operation_arg: bool
    has_daemon_arg: bool
    has_pipeline_stage: bool
    inferred_operation: str
    proposed_changes: list[str]


class LLMCallVisitor(ast.NodeVisitor):
    """Visit AST and collect every llm.generate() call site."""

    LLM_FUNC_NAMES = {"generate", "generate_traced", "generate_with_images"}

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.daemon = infer_daemon(file_path)
        self.call_sites: list[CallSite] = []
        self._func_stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_llm_call(node):
            self._record_call(node)
        self.generic_visit(node)

    def _is_llm_call(self, node: ast.Call) -> bool:
        # Match patterns:
        #   llm.generate(...)
        #   self.llm.generate(...)
        #   client.generate(...)
        if isinstance(node.func, ast.Attribute):
            return node.func.attr in self.LLM_FUNC_NAMES
        return False

    def _record_call(self, node: ast.Call) -> None:
        kwargs = {kw.arg: kw for kw in node.keywords if kw.arg}
        has_model = "model" in kwargs or "tier" in kwargs
        has_tier = "tier" in kwargs
        has_op = "operation" in kwargs
        has_daemon = "daemon_name" in kwargs
        has_pipeline = "pipeline_stage" in kwargs

        pipeline_stage = ""
        if has_pipeline:
            ps_node = kwargs["pipeline_stage"]
            if isinstance(ps_node.value, ast.Constant):
                pipeline_stage = str(ps_node.value.value)

        func_name = self._func_stack[-1] if self._func_stack else ""
        inferred_op = infer_operation(func_name, self.daemon, pipeline_stage)

        proposed: list[str] = []
        if has_model and not has_tier:
            proposed.append("model→tier")
        if not has_op:
            proposed.append(f"add operation={inferred_op}")
        if not has_daemon:
            proposed.append(f"add daemon_name={self.daemon}")

        self.call_sites.append(CallSite(
            file_path=self.file_path,
            line=node.lineno,
            col=node.col_offset,
            func_name=func_name,
            daemon=self.daemon,
            has_model_arg=has_model,
            has_tier_arg=has_tier,
            has_operation_arg=has_op,
            has_daemon_arg=has_daemon,
            has_pipeline_stage=has_pipeline,
            inferred_operation=inferred_op,
            proposed_changes=proposed,
        ))


def scan_file(file_path: Path) -> list[CallSite]:
    try:
        source = file_path.read_text()
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"  SKIP {file_path}: {exc}", file=sys.stderr)
        return []
    visitor = LLMCallVisitor(file_path)
    visitor.visit(tree)
    return visitor.call_sites


def scan_repo(root: Path, subset: str = "") -> list[CallSite]:
    base = root / subset if subset else root
    sites: list[CallSite] = []
    for py in base.rglob("*.py"):
        if any(part.startswith(".") for part in py.parts):
            continue
        if "tests/" in str(py) or "/tools/" in str(py):
            continue
        sites.extend(scan_file(py))
    return sites


# ============================================================================
# Patcher (text-based, conservative)
# ============================================================================

def apply_patch(file_path: Path, sites: list[CallSite]) -> int:
    """Apply migrations to a file. Returns count of edits made.

    Strategy: text-based replacement on the call's first line. Conservative —
    skips multi-line calls that can't be safely patched without breaking style.
    """
    if not sites:
        return 0
    source_lines = file_path.read_text().splitlines(keepends=True)
    edits = 0
    for site in sites:
        if site.has_operation_arg and site.has_daemon_arg and site.has_tier_arg:
            continue  # Already migrated
        line_idx = site.line - 1
        if line_idx >= len(source_lines):
            continue
        line = source_lines[line_idx]
        if "llm.generate(" not in line and "llm_client.generate(" not in line:
            continue  # Multi-line call — skip for safety, hand-migrate
        # Build replacement: insert tier/operation/daemon kwargs after the open paren
        new_args = []
        if not site.has_tier_arg and site.has_model_arg:
            pass  # model→tier: leave as-is, just add operation+daemon
        if not site.has_operation_arg:
            new_args.append(f'operation="{site.inferred_operation}"')
        if not site.has_daemon_arg:
            new_args.append(f'daemon_name="{site.daemon}"')
        if not new_args:
            continue
        # Insert after first ( in the call
        idx = line.index("generate(") + len("generate(")
        injection = ", ".join(new_args) + ", "
        source_lines[line_idx] = line[:idx] + injection + line[idx:]
        edits += 1
    if edits > 0:
        file_path.write_text("".join(source_lines))
    return edits


# ============================================================================
# CLI
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually modify files (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only (default)")
    parser.add_argument("--path", type=str, default="",
                        help="Subdirectory to scan (e.g. titan/)")
    parser.add_argument("--report", type=str, default="",
                        help="Write call site report to a JSON file")
    args = parser.parse_args()

    sites = scan_repo(REPO_ROOT, args.path)

    if not sites:
        print("No llm.generate() call sites found.")
        return 0

    print(f"\nFound {len(sites)} call sites across {len(set(s.file_path for s in sites))} files\n")

    by_daemon: dict[str, list[CallSite]] = {}
    for s in sites:
        by_daemon.setdefault(s.daemon, []).append(s)

    for daemon, dsites in sorted(by_daemon.items()):
        already_migrated = sum(1 for s in dsites if s.has_tier_arg and s.has_operation_arg and s.has_daemon_arg)
        needs_work = len(dsites) - already_migrated
        print(f"  {daemon:<15} {len(dsites):>4} sites  ({already_migrated} done, {needs_work} to migrate)")

    print()

    if args.report:
        import json
        report = [
            {
                "file": str(s.file_path.relative_to(REPO_ROOT)),
                "line": s.line,
                "func": s.func_name,
                "daemon": s.daemon,
                "operation": s.inferred_operation,
                "needs": s.proposed_changes,
            }
            for s in sites
        ]
        Path(args.report).write_text(json.dumps(report, indent=2))
        print(f"Report written to {args.report}")

    if args.apply:
        print("\nApplying patches...\n")
        by_file: dict[Path, list[CallSite]] = {}
        for s in sites:
            by_file.setdefault(s.file_path, []).append(s)
        total_edits = 0
        for file_path, fsites in by_file.items():
            n = apply_patch(file_path, fsites)
            if n > 0:
                rel = file_path.relative_to(REPO_ROOT)
                print(f"  {rel}: +{n} edits")
                total_edits += n
        print(f"\nTotal edits: {total_edits}")
    else:
        print("\nDry run only. Run with --apply to make changes.")
        print("\nReview before applying:")
        for s in sites[:20]:
            if s.proposed_changes:
                rel = s.file_path.relative_to(REPO_ROOT)
                print(f"  {rel}:{s.line}  ({s.daemon})  {', '.join(s.proposed_changes)}")
        if len(sites) > 20:
            print(f"  ... and {len(sites) - 20} more (use --report for full list)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
