---
phase: 0b
plan: 01
subsystem: infra
tags: [asyncio, workflow-engine, protocol, typing, async-pipeline]

requires:
  - phase: none
    provides: standalone infrastructure change
provides:
  - async WorkflowEngine.run_async() with asyncio.gather() for parallel nodes
  - run_sync() convenience wrapper for sync callers
  - 7 @runtime_checkable Protocol types for Phases 1-7 integration surfaces
  - Titan pipeline migrated to async engine
affects: [phase-1-agent-dna, phase-2-anti-slop, phase-3-memory, phase-4-middleware, phase-5-rlm, phase-6-crypto, phase-7-thresholds]

tech-stack:
  added: [asyncio, inspect.isawaitable]
  patterns: [async-parallel-gather, protocol-contracts, sync-async-dual-path]

key-files:
  created: [shared/contracts.py]
  modified: [openjarvis/workflow/engine.py, titan/workflow_pipeline.py]

key-decisions:
  - "Keep sync run() completely unchanged for backward compat; add run_async() as new method"
  - "Use inspect.isawaitable() to support both sync and async system.ask() transparently"
  - "Add run_sync() wrapper using asyncio.run() for callers that need sync entry point"
  - "All 7 Protocol types are @runtime_checkable for startup validation"

patterns-established:
  - "Async-first engine: new code should use run_async(), sync run() is backward compat only"
  - "Protocol contracts: all integration surfaces defined upfront as @runtime_checkable Protocol types"
  - "Dual sync/async support: check inspect.isawaitable() for transparent handling"

requirements-completed: [ASYNC-01, ASYNC-02, CONTRACT-01]

duration: 3min
completed: 2026-03-29
---

# Phase 0b Plan 01: Async WorkflowEngine + Interface Contracts Summary

**Async WorkflowEngine with asyncio.gather() parallel execution and 7 Protocol contracts for all integration surfaces**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-29T20:07:26Z
- **Completed:** 2026-03-29T20:10:29Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Added `run_async()` to WorkflowEngine using `asyncio.gather()` for parallel DAG stages, with all async node execution counterparts
- Migrated Titan revenue pipeline from sync `engine.run()` to `await engine.run_async()` with sync wrapper for backward compat
- Created `shared/contracts.py` with 7 `@runtime_checkable` Protocol types defining integration surfaces for Phases 1-7

## Task Commits

Each task was committed atomically:

1. **Task 1: Add async WorkflowEngine.run_async()** - `3e2c253` (feat)
2. **Task 2: Migrate Titan pipeline to async** - `440d6cc` (feat)
3. **Task 3: Interface contracts** - `2be6eb6` (feat)

## Files Created/Modified
- `openjarvis/workflow/engine.py` - Added run_async(), run_sync(), and async node execution methods (~270 lines)
- `titan/workflow_pipeline.py` - Converted run_pipeline() to async, added run_pipeline_sync() wrapper
- `shared/contracts.py` - 7 Protocol types: DNAProvider, SlopScorer, MemoryStore, Middleware, ContextRetriever, CryptoProvider, ThresholdProvider

## Decisions Made
- Kept sync `run()` completely unchanged -- zero risk to existing callers including tests and CLI
- Used `inspect.isawaitable()` to transparently support both sync and async `system.ask()` calls
- Added `run_sync()` as a convenience wrapper using `asyncio.run()` for sync entry points
- Made all Protocol types `@runtime_checkable` so implementations can be validated at startup via `isinstance()`

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Known Stubs
None - all functionality is fully wired.

## Next Phase Readiness
- Async engine is ready for Phases 3-5 which need async middleware, memory I/O, and LLM calls
- Protocol contracts define the integration surface for all 7 remaining phases
- Existing sync callers (CLI optimize command, tests) continue to work unchanged via `run()`

## Self-Check: PASSED

All files verified present, all 3 commit hashes confirmed in git log.

---
*Phase: 0b*
*Completed: 2026-03-29*
