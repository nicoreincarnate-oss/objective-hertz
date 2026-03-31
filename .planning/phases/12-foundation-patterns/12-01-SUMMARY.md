---
phase: "12"
plan: "01"
subsystem: "shared, openjarvis/security, perseus"
tags: [foundation, state-machine, atomic-checkout, recursion-guard, forbidden-tokens, session-health]
dependency_graph:
  requires: [phase-11-budget-consolidation]
  provides: [agent-state-machine, atomic-task-checkout, recursion-guard, forbidden-token-scanner, session-health]
  affects: [shared/agent_base.py, shared/db.py, shared/middleware.py, shared/daemon_memory.py, perseus/agent_registry.py]
tech_stack:
  added: []
  patterns: [state-machine, for-update-skip-locked, savepoint, regex-scanner, session-health-tracking]
key_files:
  created:
    - shared/agent_state.py
    - openjarvis/security/forbidden_tokens.py
    - .forbidden-tokens-allow
    - scripts/migrations/025-foundation-patterns.sql
    - tests/test_agent_state.py
    - tests/test_atomic_checkout.py
    - tests/test_recursion_guard.py
    - tests/test_forbidden_tokens.py
    - tests/test_session_health.py
  modified:
    - shared/agent_base.py
    - shared/db.py
    - shared/middleware.py
    - shared/daemon_memory.py
    - perseus/agent_registry.py
    - Makefile
decisions:
  - "Patch shared.agent_base.db in tests for cross-module DB mock isolation"
  - "f-string SQL for lock_clause uses hardcoded constants only (not user input), matches plan spec"
  - "forbidden_token middleware delegates redaction to credential_stripper (existing patterns)"
  - "Session health flush in _save_memory (deregistration path) ensures final snapshot persisted"
metrics:
  duration: "22min"
  completed: "2026-03-31"
---

# Phase 12 Plan 01: Foundation Patterns Summary

5 infrastructure patterns ported from Paperclip (MIT) into Objective Hertz -- agent state machine with 7-state enum and transition matrix, atomic task checkout with FOR UPDATE SKIP LOCKED and SAVEPOINT, recursion guard with depth tracking and configurable max, forbidden token scanner with 14 regex patterns and middleware integration, session health with auto-reset at 2M tokens or 72h.

## What Was Built

### Pattern 1: Agent State Machine (FP-01)
- `shared/agent_state.py`: AgentState enum (7 states), PauseReason enum, TRANSITION_MATRIX, validate_transition()
- AgentBase._transition() method with DB persistence and event emission
- State transitions wired at register/claim/complete/fail/shutdown boundaries
- agent_registry query includes state + pause_reason columns
- Feature flag: AGENT_STATE_MACHINE_ENABLED

### Pattern 2: Atomic Task Checkout (FP-02)
- get_pending_tasks() appends FOR UPDATE SKIP LOCKED when flag is ON
- claim_task() uses SAVEPOINT/ROLLBACK/RELEASE for transactional claims
- Prevents double-claim race conditions between concurrent agents
- Feature flag: ATOMIC_CHECKOUT_ENABLED

### Pattern 3: Recursion Guard (FP-03)
- insert_task() accepts depth parameter (default 0)
- claim_task() rejects tasks exceeding configurable max_task_depth
- spawn_child_task() propagates parent depth + 1
- Feature flag: RECURSION_GUARD_ENABLED

### Pattern 4: Forbidden Token Scanner (FP-04)
- `openjarvis/security/forbidden_tokens.py`: 14 regex patterns covering API keys, DSNs, injection markers, OS username
- forbidden_token_middleware at position 7 (output sanitizer after telemetry)
- .forbidden-tokens-allow file for suppressing known-safe patterns
- Makefile forbidden-tokens target scans templates directory
- Feature flag: FORBIDDEN_TOKEN_SCAN_ENABLED

### Pattern 5: Session Health (FP-05)
- WorkingMemory extended with total_tokens, elapsed_seconds, error_count, state_transitions, context_saturation_pct
- needs_reset() triggers at 2M tokens or 72h elapsed
- Auto-reset at claim_task entry with event emission and snapshot persistence
- _flush_session_health() persists to session_health table
- Feature flag: SESSION_HEALTH_ENABLED

### Migration 025
- agent_registry: +state (VARCHAR 20), +pause_reason (VARCHAR 20)
- task_queue: +depth (INTEGER DEFAULT 0)
- session_health table with agent_id index
- 6 feature flag seeds in system_config (all OFF)
- Fully idempotent: IF NOT EXISTS / ON CONFLICT DO NOTHING

## Tests

30 new tests across 5 test files:
- test_agent_state.py: 7 tests (enum transitions, matrix completeness, flag passthrough)
- test_atomic_checkout.py: 5 tests (lock clause, savepoint, flag passthrough)
- test_recursion_guard.py: 5 tests (depth propagation, rejection, configurable max)
- test_forbidden_tokens.py: 8 tests (detection, allowlist, middleware redaction)
- test_session_health.py: 5 tests (accumulation, threshold, reset, snapshot)

All existing tests continue to pass with all flags OFF.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 1fdf5dd | chore | Migration 025: foundation patterns schema |
| 06c4b17 | feat | Agent state machine with enum transitions |
| 00664fa | feat | Recursion guard with depth tracking |
| b8436c7 | feat | Atomic task checkout with SKIP LOCKED |
| bb4d7cf | feat | Forbidden token scanner + middleware |
| a4085b2 | feat | Session health tracking with auto-reset |
| 938b961 | fix | Test isolation for cross-module DB mocks |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test isolation with conftest module restoration**
- **Found during:** Test verification (all patterns)
- **Issue:** conftest.py's _PROTECTED_MODULES guard swaps shared.db between test modules, causing patches on shared.db.fetch_one to target a different module object than agent_base.db
- **Fix:** Patched at point-of-use (shared.agent_base.db) with _mock_db() helper instead of shared.db
- **Files modified:** All 5 test files
- **Commit:** 938b961

**2. [Rule 2 - Missing] Integer coercion for max_task_depth config value**
- **Found during:** Pattern 3 implementation
- **Issue:** get_config() returns JSONB which may be string "5" instead of int 5
- **Fix:** Added `if isinstance(max_depth, str): max_depth = int(max_depth)` guard
- **Files modified:** shared/agent_base.py

## Known Stubs

None -- all 5 patterns are fully implemented with working feature flags.

## Self-Check: PASSED

- [x] shared/agent_state.py exists
- [x] openjarvis/security/forbidden_tokens.py exists
- [x] scripts/migrations/025-foundation-patterns.sql exists
- [x] .forbidden-tokens-allow exists
- [x] All 5 test files exist
- [x] All 7 commits verified in git log
- [x] 30 new tests pass
- [x] ruff check passes on all modified files
- [x] No f-string SQL with user input (lock_clause/base_where are hardcoded constants)
