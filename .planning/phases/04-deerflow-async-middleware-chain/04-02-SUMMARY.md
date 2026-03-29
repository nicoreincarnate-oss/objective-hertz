---
phase: 4
plan: 04-02
subsystem: openjarvis/workflow, tests/shared
tags: [middleware, engine-integration, tests, feature-flag, async]
dependency_graph:
  requires: [04-01-middleware-chain]
  provides: [engine-middleware-wiring, middleware-test-suite]
  affects: [openjarvis-workflow-engine, titan-pipeline, clawdbot-pipeline]
tech_stack:
  added: [asyncio-bridge-pattern]
  patterns: [feature-flag-gating, sys-modules-mocking, lazy-import-testing]
key_files:
  created:
    - tests/shared/test_middleware.py
    - tests/shared/__init__.py
  modified:
    - openjarvis/workflow/engine.py
decisions:
  - "Engine middleware gated by ENABLE_MIDDLEWARE env var (zero change when off)"
  - "Async middleware chain bridged to sync engine via asyncio.run() with event-loop detection"
  - "Fallback to direct execution if middleware import or chain fails"
  - "Stage context includes daemon_name, stage_name, pipeline, tools for middleware"
  - "Tests use sys.modules patching for lazy imports (not patch(create=True))"
metrics:
  duration: 8min
  completed: "2026-03-29T22:34:00Z"
  tasks: 2
  files: 3
---

# Phase 4 Plan 02: Engine Integration + Tests Summary

WorkflowEngine middleware integration behind ENABLE_MIDDLEWARE feature flag with async-to-sync bridge pattern, plus 42-test comprehensive suite covering all middleware components, chain ordering, and feature flag behavior.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | WorkflowEngine middleware integration | 150ff3c | openjarvis/workflow/engine.py |
| 2 | Full test suite (42 tests) | da528e4 | tests/shared/test_middleware.py, tests/shared/__init__.py, shared/middleware.py |

## Key Implementation Details

### Engine Integration (Task 1)
- `_middleware_enabled()` static method checks `ENABLE_MIDDLEWARE` env var
- `_execute_node()` dispatches to `_execute_node_with_middleware()` or `_execute_node_core()`
- `_execute_node_with_middleware()` builds stage context and runs async chain
- Async-to-sync bridge detects running event loop to avoid deadlocks
- Graceful fallback: if middleware import fails or chain errors, falls back to direct execution
- Stage context: `daemon_name`, `stage_name`, `pipeline`, `tools`

### Test Suite (Task 2)
- 42 tests across 8 test classes
- Chain core (7): ordering, short-circuit, context modification, fluent API
- MemoryMiddleware (3): flag gating, injection, import failure resilience
- DNAGuardMiddleware (5): flag gating, tool blocking, authorized pass, fail-open
- AntiSlopMiddleware (5): flag gating, content/non-content stages, secret blocking
- TelemetryMiddleware (3): DB recording, failure resilience, status tracking
- BudgetCheckMiddleware (4): under/over/exact budget, fail-open
- Pipeline config (8): all pipelines, env override, registry completeness
- Full chain integration (3): all 5 MW in sequence, budget blocks early, execution order
- Engine feature flag (4): truthy/falsy value testing
- Uses `sys.modules` patching for lazy imports (middleware uses internal `from X import Y`)

## Decisions Made

1. Engine middleware gated by ENABLE_MIDDLEWARE env var (zero change when off)
2. Async middleware chain bridged to sync engine via asyncio.run() with event-loop detection
3. Fallback to direct execution if middleware import or chain fails (resilience)
4. Stage context includes daemon_name, stage_name, pipeline, tools for middleware consumption
5. Tests use sys.modules patching for lazy imports rather than patch(create=True)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Adapted async middleware to sync engine**
- **Found during:** Task 1
- **Issue:** Plan references `_execute_node_async()` which doesn't exist; engine is sync-only
- **Fix:** Created async-to-sync bridge with event-loop detection in `_execute_node_with_middleware()`
- **Files modified:** openjarvis/workflow/engine.py
- **Commit:** 150ff3c

**2. [Rule 3 - Blocking] Fixed test runner compatibility with Python 3.14**
- **Found during:** Task 2
- **Issue:** `asyncio.get_event_loop()` raises RuntimeError in Python 3.14 without running loop
- **Fix:** Changed `_run()` helper to use `asyncio.run()` instead
- **Files modified:** tests/shared/test_middleware.py
- **Commit:** da528e4

**3. [Rule 3 - Blocking] Fixed lazy import mocking strategy**
- **Found during:** Task 2
- **Issue:** `patch("shared.middleware.X", create=True)` doesn't work for lazy `from X import Y` inside functions
- **Fix:** Used `sys.modules` patching with `_mock_module()` helper for proper lazy import interception
- **Files modified:** tests/shared/test_middleware.py
- **Commit:** da528e4

## Known Stubs

None -- all implementations are fully wired and tested.

## Verification

- `ruff check tests/shared/test_middleware.py` -- clean
- `ruff check openjarvis/workflow/engine.py` -- clean (pre-existing typing deprecation warnings only)
- 42/42 middleware tests pass
- 15/15 existing workflow engine tests pass (no regression)
- AST parse valid on both files

## Self-Check: PASSED

All 4 files found on disk. Both commit hashes verified in git log.
