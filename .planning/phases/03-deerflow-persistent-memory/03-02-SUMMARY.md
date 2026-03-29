---
phase: 3
plan: 03-02
subsystem: shared/agent_base, perseus/scheduler, tests
tags: [memory, lifecycle, scheduler, tests, deerflow]
dependency_graph:
  requires: [03-01]
  provides: [memory-lifecycle-hooks, memory-cleanup-job, memory-test-suite]
  affects: [shared/agent_base.py, perseus/scheduler.py]
tech_stack:
  added: []
  patterns: [feature-flag-gating, cache-first-startup, background-reconciliation]
key_files:
  created:
    - tests/shared/test_daemon_memory.py
    - tests/shared/test_memory_cache.py
  modified:
    - shared/agent_base.py
    - perseus/scheduler.py
decisions:
  - "_load_memory uses cache-first startup with background Postgres reconciliation"
  - "_save_memory persists all WorkingMemory entries as episodic (survives restarts)"
  - "Memory init failure is non-fatal -- daemon operates without memory"
  - "Cleanup job runs as non-skippable daily schedule in Perseus"
metrics:
  duration: 4min
  completed: "2026-03-29T22:12:25Z"
---

# Phase 3 Plan 02: Daemon Integration + Scheduler + Tests Summary

Memory lifecycle hooks wired into AgentBase register/deregister with cache-first startup and background Postgres reconciliation, daily cleanup job in Perseus scheduler, 41 tests covering all memory tiers.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Memory injection into AgentBase | b58a4a5 | shared/agent_base.py |
| 2 | Perseus daily cleanup job | 851cbf5 | perseus/scheduler.py |
| 3 | Test suite (41 tests) | 2a1d4ef | tests/shared/test_daemon_memory.py, tests/shared/test_memory_cache.py |

## What Was Built

### Task 1: AgentBase Memory Lifecycle

Added to `shared/agent_base.py`:
- `_memory`, `_memory_cache`, `_working_memory` attributes (initialized to None)
- `_load_memory()` called during `register()` -- loads from JSON cache first, then background reconciles with Postgres
- `_save_memory()` called during `deregister()` -- persists all WorkingMemory entries as episodic memories
- `_reconcile_memory()` background task for Postgres authoritative sync
- `_deerflow_memory_enabled()` feature flag check
- All operations gated behind `ENABLE_DEERFLOW_MEMORY` env var
- Non-fatal: daemon operates normally if memory init fails

### Task 2: Perseus Cleanup Job

Added `memory_cleanup` to `perseus/scheduler.py`:
- 86400s interval (daily), non-skippable
- Triggers `DaemonMemoryStore.cleanup_expired()` for expired episodic entries
- Global sweep (no daemon_name filter) cleans all daemons

### Task 3: Test Suite (41 tests)

**test_daemon_memory.py** (32 tests):
- WorkingMemory: set/get, eviction at max_items, LRU update, delete, clear, items, len
- DaemonMemoryStore episodic: save, load, cleanup_expired (per-daemon and global), Protocol save, unknown type
- DaemonMemoryStore semantic: save, compression trigger at cap
- Memory isolation: same-daemon allowed, cross-daemon denied, domain-allowed, IsolatedMemoryStore
- Protocol compliance: DaemonMemoryStore and IsolatedMemoryStore implement MemoryStore
- Feature flag: off by default, on with "true"/"1", off with "false"/""
- AgentBase hooks: register loads memory when enabled, skips when disabled, deregister saves
- Stats view: mock query on daemon_memory_stats

**test_memory_cache.py** (9 tests):
- Round-trip: save + load, empty returns dict, corrupt cache returns empty, overwrite
- Startup time: no DB calls during cache load
- Write-through: save_with_cache writes to both DB and cache
- Invalidation: removes file, nonexistent is noop
- Rebuild: rebuilds from Postgres and writes to disk

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Merged intel-integration branch for Wave 1 dependencies**
- **Found during:** Task 3 (test execution)
- **Issue:** Worktree branch diverged before Wave 1 (03-01) commits; `shared/daemon_memory.py` did not exist
- **Fix:** Merged intel-integration branch, resolved merge conflict in shared/agent_base.py (kept both `os` and `time` imports)
- **Files modified:** All files from intel-integration merge
- **Commit:** 00984ab

## Acceptance Criteria

- [x] AgentBase register/deregister include memory hooks
- [x] Daily cleanup scheduled in Perseus
- [x] All 41 tests pass
- [x] Feature flag off = zero change to behavior
- [x] `ruff check shared/agent_base.py shared/daemon_memory.py` clean

## Known Stubs

None -- all memory operations are fully wired to DaemonMemoryStore and MemoryCache from Wave 1.

## Self-Check: PASSED

- All 5 key files: FOUND
- All 4 commits (b58a4a5, 851cbf5, 00984ab, 2a1d4ef): FOUND
- 41/41 tests passing
- ruff check clean on all modified files
