---
phase: "11"
plan: "11"
subsystem: budget-enforcement
tags: [budget, middleware, fail-closed, consolidation]
dependency_graph:
  requires: [phase-04-middleware-chain]
  provides: [consolidated-budget-authority, fail-closed-budget]
  affects: [shared/middleware.py, shared/llm_client.py, tools/budget_guard.py]
tech_stack:
  added: []
  patterns: [fail-closed-on-db-error, single-budget-authority, config-driven-cap]
key_files:
  created: []
  modified:
    - shared/middleware.py
    - shared/llm_client.py
    - tools/budget_guard.py
    - tests/shared/test_middleware.py
    - tests/test_budget_gate.py
    - tests/test_budget_sources.py
    - tests/test_llm_local_routing.py
decisions:
  - "ENABLE_CONSOLIDATED_BUDGET uses os.environ.get with empty default (not _flag) because _flag defaults to true for security flags"
  - "check_budget_for_llm_call hardcoded ON after legacy removal (no flag check)"
  - "Budget cap reads from config.budget.monthly_cap via _get_budget_cap() with $800 fallback"
  - "_budget_gate fully removed in Commit 2 (two-commit cutover strategy)"
metrics:
  duration: "8min"
  completed: "2026-03-31"
  tasks: 13
  files: 7
  tests_added: 16
  tests_total: 71
---

# Phase 11: Budget Consolidation Summary

Consolidated four scattered budget enforcement points into shared/middleware.py:check_budget_for_llm_call as the single authority, failing closed on DB errors (returns "local" for Ollama fallback).

## What Changed

### Commit 1: Consolidation (fa9d6a5)
- Added `check_budget_for_llm_call()` to `shared/middleware.py` -- standalone async function that queries `v_effective_budget_tracking` view, computes percent_used, and returns either the requested model or `"local"` for Ollama
- Upgraded `budget_check_middleware` with consolidated path gated by `ENABLE_CONSOLIDATED_BUDGET` env var -- handles both per-LLM-call checks (via `requested_model` in ctx) and pipeline-stage blocking
- Wired `LLMClient.generate()` and `generate_with_images()` to use consolidated path when flag ON, legacy `_budget_gate` when OFF
- Replaced hardcoded `_BUDGET_CAP_USD = 800.0` with `_get_budget_cap()` that reads from `config.budget.monthly_cap`
- Added `_ALERT_THRESHOLD = 0.8` constant for 80% downgrade trigger

### Commit 2: Test Suite (ff5b398)
- 7 tests in `TestConsolidatedBudgetMiddleware`: under/over budget, threshold downgrade, DB failure fail-closed, pipeline stage blocking
- 5 tests in `TestCheckBudgetForLlmCall`: flag behavior, exceed, DB error, threshold
- 2 tests in `TestConsolidatedBudgetFeatureFlag`: flag routing, legacy preservation
- 2 regression tests: middleware uses `v_effective_budget_tracking`, fails closed

### Commit 3: Legacy Removal (855974e)
- Deleted `_budget_gate` method from `LLMClient` entirely
- Replaced conditional flag checks with direct `check_budget_for_llm_call` calls
- Removed `ENABLE_CONSOLIDATED_BUDGET` flag guard from `check_budget_for_llm_call` (always executes)
- Updated `budget_guard.py` docstring to clarify reporting-only role
- Rewrote `test_budget_gate.py` as `TestConsolidatedBudgetRouting` (end-to-end LLM routing tests)
- Updated `test_llm_local_routing.py` to mock `check_budget_for_llm_call` instead of `_budget_gate`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing functionality] ENABLE_CONSOLIDATED_BUDGET flag default**
- **Found during:** Task 1
- **Issue:** `_flag()` helper defaults to `"true"` for security flags, but `ENABLE_CONSOLIDATED_BUDGET` needs to default to `false`
- **Fix:** Used `os.environ.get("ENABLE_CONSOLIDATED_BUDGET", "").lower() in ("true", "1", "yes")` instead of `_flag()`
- **Files modified:** shared/middleware.py
- **Commit:** fa9d6a5

**2. [Rule 3 - Blocking issue] Pre-existing test_budget_gate fixture missing get_config**
- **Found during:** Task 1 (test verification)
- **Issue:** `tests/test_budget_gate.py` fixture created fake `shared.db` module without `get_config`, causing ImportError since `shared.llm_client` imports `get_config` from `shared.db`
- **Fix:** Added `fake_db.get_config = AsyncMock(return_value=None)` to fixture
- **Files modified:** tests/test_budget_gate.py
- **Commit:** fa9d6a5

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Use `os.environ.get` not `_flag()` for ENABLE_CONSOLIDATED_BUDGET | `_flag()` defaults true (security pattern); budget consolidation needs default false for safe rollout |
| check_budget_for_llm_call hardcoded ON after legacy removal | Two-commit strategy: Commit 1 is safe rollback, Commit 3 removes flag after verification |
| _get_budget_cap() reads from config with $800 fallback | Aligns with existing pattern in _budget_gate and BudgetGuard |
| Frozen dataclass bypass via object.__setattr__ in tests | config is frozen dataclass; tests need to set api_key for generate() path testing |

## Verification

- 71 tests passing across 4 test files
- `ruff check` clean on all 7 modified files
- AEGIS SQL safety: zero f-string SQL in shared/middleware.py
- Budget cap reads from `config.budget.monthly_cap` (not hardcoded)
- DB error returns "local" (fail-closed) in both check_budget_for_llm_call and budget_check_middleware
- `_record_claude_spend` untouched (cost recording orthogonal to enforcement)
- `budget_guard.py` is reporting-only (docstring updated)

## Known Stubs

None -- all functionality is fully wired.

## Self-Check: PASSED
