#!/usr/bin/env python3
"""Phase 42 verification script — checks the MAGMA runtime is healthy.

Usage:
    python scripts/verify_magma_runtime.py
    python scripts/verify_magma_runtime.py --verbose

Exit codes:
    0 — all checks passed
    1 — Postgres or core modules missing (hard fail)
    2 — Ollama or API down (warning only)
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── ANSI colors ──────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS = f"{GREEN}✅{RESET}"
FAIL = f"{RED}❌{RESET}"
WARN = f"{YELLOW}⚠ {RESET}"

# ── Phase 42 module list ─────────────────────────────────────────────
PHASE42_MODULES = [
    "scripts.bootstrap_magma",
    "shared.magma_writer",
    "shared.consolidator",
    "shared.procedural_extractor",
    "shared.edge_inference",
    "shared.magma_edge_types",
    "shared.operator_memory_hook",
    "shared.magma",
]


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "fail" | "warn"
    message: str = ""
    severity: str = "hard"  # "hard" or "soft"
    details: list[str] = field(default_factory=list)


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def hard_failures(self) -> int:
        return sum(1 for r in self.results if r.status == "fail" and r.severity == "hard")

    @property
    def soft_failures(self) -> int:
        return sum(1 for r in self.results if r.status == "fail" and r.severity == "soft")

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == "warn")


# ── Individual checks ────────────────────────────────────────────────


def check_modules() -> CheckResult:
    present: list[str] = []
    missing: list[str] = []
    for mod_name in PHASE42_MODULES:
        try:
            importlib.import_module(mod_name)
            present.append(mod_name)
        except Exception:
            missing.append(mod_name)
    if not missing:
        return CheckResult(
            name="Phase 42 modules",
            status="pass",
            message=f"all {len(PHASE42_MODULES)} present",
            details=[f"  • {m}" for m in present],
        )
    if len(missing) >= len(PHASE42_MODULES) // 2:
        return CheckResult(
            name="Phase 42 modules",
            status="fail",
            message=f"{len(missing)}/{len(PHASE42_MODULES)} missing",
            details=[f"  ❌ {m}" for m in missing] + [f"  ✅ {m}" for m in present],
        )
    return CheckResult(
        name="Phase 42 modules",
        status="warn",
        message=f"{len(missing)} missing",
        severity="soft",
        details=[f"  ❌ {m}" for m in missing] + [f"  ✅ {m}" for m in present],
    )


def check_postgres() -> CheckResult:
    try:
        from shared import db  # type: ignore
    except Exception as e:
        return CheckResult(
            name="Postgres reachability",
            status="fail",
            message=f"shared.db import failed: {e}",
        )
    try:
        row = db.fetch_one("SELECT 1 AS ok")  # type: ignore[attr-defined]
        if row and (row.get("ok") == 1 or list(row.values())[0] == 1):
            return CheckResult(
                name="Postgres reachability",
                status="pass",
                message="connection pool OK",
            )
        return CheckResult(
            name="Postgres reachability",
            status="fail",
            message=f"unexpected row: {row}",
        )
    except Exception as e:
        return CheckResult(
            name="Postgres reachability",
            status="fail",
            message=str(e),
        )


def _table_exists(table_name: str) -> bool:
    from shared import db  # type: ignore
    row = db.fetch_one(
        "SELECT 1 AS exists FROM information_schema.tables WHERE table_name = %s LIMIT 1",
        (table_name,),
    )
    return bool(row)


def _table_count(table_name: str) -> int | None:
    from shared import db  # type: ignore
    try:
        row = db.fetch_one(f"SELECT COUNT(*) AS c FROM {table_name}")  # nosec - table_name from allowlist
        if not row:
            return 0
        return int(row.get("c") or list(row.values())[0])
    except Exception:
        return None


def check_magma_tables() -> CheckResult:
    required = ["memory_provenance", "magma_nodes", "magma_edges"]
    found: list[str] = []
    missing: list[str] = []
    try:
        for t in required:
            if _table_exists(t):
                found.append(t)
            else:
                missing.append(t)
    except Exception as e:
        return CheckResult(
            name="MAGMA tables",
            status="fail",
            message=f"query failed: {e}",
        )
    if not missing:
        return CheckResult(
            name="MAGMA tables",
            status="pass",
            message=f"all {len(required)} present",
            details=[f"  ✅ {t}" for t in found],
        )
    return CheckResult(
        name="MAGMA tables",
        status="fail",
        message=f"missing: {', '.join(missing)}",
        details=[f"  ✅ {t}" for t in found] + [f"  ❌ {t}" for t in missing],
    )


def check_node_count() -> CheckResult:
    n = _table_count("magma_nodes")
    if n is None:
        return CheckResult(name="magma_nodes count", status="fail", message="query failed")
    if n == 0:
        return CheckResult(
            name="magma_nodes count",
            status="warn",
            message="0 nodes — bootstrap not run yet?",
            severity="soft",
        )
    return CheckResult(name="magma_nodes count", status="pass", message=f"{n} nodes")


def check_edge_count() -> CheckResult:
    n = _table_count("magma_edges")
    if n is None:
        return CheckResult(name="magma_edges count", status="fail", message="query failed")
    if n == 0:
        return CheckResult(
            name="magma_edges count",
            status="warn",
            message="0 edges — edge inference not run yet?",
            severity="soft",
        )
    return CheckResult(name="magma_edges count", status="pass", message=f"{n} edges")


def check_recent_activity() -> CheckResult:
    try:
        from shared import db  # type: ignore
        row = db.fetch_one(
            "SELECT COUNT(*) AS c FROM magma_nodes WHERE created_at > NOW() - INTERVAL '24 hours'"
        )
        n = int(row.get("c") or 0) if row else 0
    except Exception as e:
        return CheckResult(
            name="Recent activity (24h)",
            status="warn",
            message=f"query failed: {e}",
            severity="soft",
        )
    if n == 0:
        return CheckResult(
            name="Recent activity (24h)",
            status="warn",
            message="no new nodes in last 24h",
            severity="soft",
        )
    return CheckResult(
        name="Recent activity (24h)",
        status="pass",
        message=f"{n} nodes in last 24h",
    )


def _http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        req = Request(url, headers={"User-Agent": "verify-magma/1.0"})
        with urlopen(req, timeout=timeout) as resp:  # nosec - localhost only
            code = resp.getcode()
            return (200 <= code < 300, f"HTTP {code}")
    except URLError as e:
        return (False, str(e.reason))
    except Exception as e:
        return (False, str(e))


def check_ollama() -> CheckResult:
    ok, msg = _http_ok("http://localhost:11434/api/tags")
    if ok:
        return CheckResult(name="Ollama", status="pass", message=msg)
    return CheckResult(
        name="Ollama",
        status="fail",
        message=msg,
        severity="soft",
    )


def check_api() -> CheckResult:
    candidates = [
        "http://localhost:8500/api/memory/graph?depth=1&limit=10",
        "http://localhost:8000/api/memory/graph?depth=1&limit=10",
    ]
    for url in candidates:
        ok, msg = _http_ok(url)
        if ok:
            return CheckResult(name="Memory API", status="pass", message=f"{url} → {msg}")
    return CheckResult(
        name="Memory API",
        status="fail",
        message="no Hermes memory endpoint reachable on :8500 or :8000",
        severity="soft",
    )


# ── Runner ───────────────────────────────────────────────────────────


CHECKS: list[Callable[[], CheckResult]] = [
    check_modules,
    check_postgres,
    check_magma_tables,
    check_node_count,
    check_edge_count,
    check_recent_activity,
    check_ollama,
    check_api,
]


def render(report: Report, verbose: bool) -> None:
    print(f"\n{BOLD}{CYAN}── MAGMA Runtime Verification ──{RESET}\n")
    width = max(len(r.name) for r in report.results) + 2
    for r in report.results:
        if r.status == "pass":
            marker = PASS
        elif r.status == "warn":
            marker = WARN
        else:
            marker = FAIL
        print(f"  {marker}  {r.name.ljust(width)}  {r.message}")
        if verbose and r.details:
            for line in r.details:
                print(f"      {line}")

    print()
    if report.hard_failures > 0:
        line = f"{RED}{BOLD}OVERALL HEALTH: CRITICAL{RESET} — {report.hard_failures} hard failures"
    elif report.soft_failures > 0 or report.warnings > 0:
        line = (
            f"{YELLOW}{BOLD}OVERALL HEALTH: DEGRADED{RESET} — "
            f"{report.soft_failures} soft fails, {report.warnings} warnings"
        )
    else:
        line = f"{GREEN}{BOLD}OVERALL HEALTH: HEALTHY{RESET} — all checks passed"
    print(line)
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify MAGMA runtime health (Phase 42).")
    parser.add_argument("--verbose", "-v", action="store_true", help="show per-check details")
    args = parser.parse_args(argv)

    report = Report()
    started = time.time()
    for check in CHECKS:
        try:
            report.add(check())
        except Exception as e:  # last-ditch safety net
            report.add(CheckResult(name=check.__name__, status="fail", message=f"crashed: {e}"))
    elapsed = time.time() - started

    render(report, verbose=args.verbose)
    print(f"  ({len(report.results)} checks in {elapsed:.2f}s)\n")

    if report.hard_failures > 0:
        return 1
    if report.soft_failures > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
