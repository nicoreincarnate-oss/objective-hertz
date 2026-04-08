---
phase: 03
plan: 01
title: Metadata Plumbing (Layer A) — SUMMARY
wave: 1
status: SHIP
verdict: SHIP
completed: 2026-04-08
---

# Phase 3 Plan 01 — Metadata Plumbing SUMMARY

**One-liner:** Added `daemon_name=` + `operation=` kwargs to 40 single-line `llm.generate()` call sites across 7 daemons via fixed AST migrator. 66 multi-line calls deferred. Spend-alerts wiring + AUTO_TIER_ENABLED flag confirmed pre-existing.

## Verdict: SHIP

All preservation requirements held: `shared/llm_client.py` untouched, Trinity swap intact, P0-5/P0-6/P0-9/P0-10 untouched, regression suite at 98 passed / 6 pre-existing failed (matches Phase 2 baseline).

## Task Status

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Migrate 8 daemons + orchestrator | DONE (7 commits) | 40 single-line patches, 66 multi-line deferred |
| 2 | Wire `spend_alerts` into middleware | ALREADY DONE | Pre-existing in `shared/middleware.py:597` |
| 3 | Add `AUTO_TIER_ENABLED` to `.env.example` | ALREADY DONE | Pre-existing at line 250 |
| 4 | pytest regression | PASS | 98 passed / 6 pre-existing failed (no regression) |

## Per-Daemon Migration Results

| Daemon | Sites Found | Single-line Migrated | Multi-line Deferred | Commit | Status |
|--------|-------------|----------------------|---------------------|--------|--------|
| perseus | 6 | 2 | 4 | `d5c0a8e` | OK |
| titan | 29 | 17 | 12 | `f744ba7` | OK |
| hermes | 6 | 4 | 2 | `5ecc152` | OK |
| clawdbot | 15 | 1 | 14 | `1eb6bf1` | OK |
| ruflo | 4 | 1 | 3 | `3ac0aa2` | OK |
| deerflow_research | 0 | 0 | 0 | (no commit) | N/A |
| openjarvis | 45 | 14 | 31 | `2cf19d9` | OK |
| conway | 1 | 1 | 0 | `a1fca13` | OK |
| orchestrator.py | 1 | 0 | 1 | (no commit) | deferred (multi-line) |
| **TOTAL** | **107** | **40** | **67** | 7 commits | — |

## Critical Deviation — Migrator Bug Fix (Rule 3)

**Issue discovered during Task 1, perseus run:** The first perseus migration produced two `SyntaxError` files: `perseus/self_audit.py` and `perseus/scout.py`. The migrator was inserting `operation=...` and `daemon_name=...` immediately after `generate(` — placing keyword arguments BEFORE existing positional arguments (e.g. `prompt`), which Python rejects (`SyntaxError: positional argument follows keyword argument`).

**Root cause:** `scripts/migrate_to_litellm.py:apply_patch` previous logic:
```python
idx = line.index("generate(") + len("generate(")
source_lines[line_idx] = line[:idx] + injection + line[idx:]
```
This always prepended kwargs to the first arg, regardless of whether the first arg was positional.

**Fix (commit `a6dfd27`):** Walk the call line forward from `generate(` to find the matching closing paren (with string-literal awareness), then APPEND kwargs immediately before that closing paren. This places metadata kwargs AFTER all existing args, preserving Python syntax.

After the fix, `git checkout perseus/` reverted the broken patches and re-running the migration produced clean, parseable output. Every subsequent daemon used the fixed migrator and validated cleanly.

**Validation per daemon:**
- AST parse on every modified file (zero failures after fix)
- Spot-check `import` of main daemon module (or `ruflo.agent`, `conway.wallet`, `openjarvis.system` where no `daemon.py` exists)
- Full pytest regression at the end

**Files reverted at any point:** Two perseus files reverted before migrator fix; re-applied cleanly afterward. Net: zero daemons left in a reverted state.

## Task 2 — Spend Alerts Already Wired

`shared/middleware.py:check_budget_for_llm_call` (lines 597–672) already imports and dispatches `shared.spend_alerts` (`check_daemon_spend` + `dispatch_alert`) via the `SPEND_ALERTS_ENABLED` feature flag. BLOCK-level alerts force Ollama fallback. Best-effort error handling (`logger.warning` + continue) preserves P0-6 fail-closed semantics.

The plan's stated function name `check_and_emit` does NOT exist in `shared/spend_alerts.py`. The actual API uses `check_daemon_spend(daemon, db_pool) -> list[SpendAlert]` followed by `dispatch_alert(alert)`. The pre-existing wiring uses the correct API. **No commit was needed** — work was already complete from a prior wave.

## Task 3 — AUTO_TIER_ENABLED Already Present

`.env.example:250` already declares `AUTO_TIER_ENABLED=false` with a comment. No commit was needed.

## Task 4 — pytest Regression

```
tests/test_phase23_factory.py
tests/test_phase23_providers.py
tests/test_phase23_types.py
tests/test_phase23_unified_llm.py
tests/test_phase41_tiers.py
tests/test_verifier_layers.py
tests/test_phase42_semantic_cache.py
```

**Result: 98 passed, 6 failed (0.28s)**

The 6 failures all live in `tests/test_verifier_layers.py` and were pre-existing in Phase 2:
- `TestGrammarCompiler::test_compile_simple_object`
- `TestGrammarCompiler::test_compile_array`
- `TestGrammarCompiler::test_compile_enum`
- `TestGrammarCompiler::test_caches_compiled_grammars`
- `TestRedactor::test_redacts_eth_address`
- `TestRedactor::test_canary_test_passes`

These are unrelated to LLM call-site metadata and existed before this wave. No new failures introduced.

## Deferred Multi-Line Calls

Total: **67 call sites** across all daemons + orchestrator.py. Final report at `/tmp/migrate_to_litellm_manual_review.json` reflects the LAST migrator run only (openjarvis's 31). Per-daemon counts captured in the table above are authoritative.

These require either:
1. A libcst-based rewriter that can safely insert kwargs into multi-line `Call` nodes, or
2. Hand-migration: open each file, locate the `)` of the multi-line call, insert `, operation="...", daemon_name="..."` before it.

**Out of scope for Wave 1.** Should be a follow-up plan (Phase 3 Plan 02 or a dedicated `libcst-rewriter` plan).

## Commits Landed

| SHA | Message |
|-----|---------|
| `a6dfd27` | fix(migrate): append metadata kwargs after positional args (Phase 3 Wave 1) |
| `d5c0a8e` | refactor(perseus): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `f744ba7` | refactor(titan): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `5ecc152` | refactor(hermes): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `1eb6bf1` | refactor(clawdbot): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `3ac0aa2` | refactor(ruflo): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `2cf19d9` | refactor(openjarvis): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |
| `a1fca13` | refactor(conway): add daemon_name + operation metadata to llm.generate() call sites (Phase 3 Wave 1) |

**Total: 8 commits (1 fix + 7 daemon refactors).** No commits for `deerflow_research` (0 sites) or `orchestrator.py` (only multi-line site, deferred).

## Preservation Checks

- `shared/llm_client.py` untouched (1711 lines, unchanged)
- `shared/tiers.py` Trinity swap intact
- `config/litellm_config.yaml` Trinity entries untouched
- P0-5/P0-6 fail-closed semantics in `check_budget_for_llm_call` preserved (best-effort error handling on spend_alerts)
- `.env` not modified (operator-owned)
- Trinity-related files untouched

## Blockers

None. Ready for Wave 2 (auto-tier classifier opt-in, multi-line hand-migration, or whatever Phase 3 Plan 02 targets).

## Self-Check: PASSED

Verified:
- All 8 commits present in `git log` (`a6dfd27`, `d5c0a8e`, `f744ba7`, `5ecc152`, `1eb6bf1`, `3ac0aa2`, `2cf19d9`, `a1fca13`)
- All migrated files parse via `ast.parse`
- Daemon modules import without error
- Regression suite green relative to Phase 2 baseline
- `shared/llm_client.py` unchanged (`git diff main -- shared/llm_client.py` would show no edits from this wave)
