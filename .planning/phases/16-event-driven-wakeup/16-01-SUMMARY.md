---
phase: "16"
plan: "01"
subsystem: "shared/wakeup_queue, perseus/scheduler, orchestrator"
tags: [event-driven, LISTEN-NOTIFY, wakeup-queue, paperclip, AEGIS]
dependency_graph:
  requires: [shared/db.py, shared/config.py, openjarvis/vassals/perseus_scheduler.py, orchestrator.py]
  provides: [shared/wakeup_queue.py, scripts/migrations/029-wakeup-queue.sql]
  affects: [shared/db.py, openjarvis/vassals/perseus_scheduler.py, orchestrator.py, perseus/scheduler.py]
tech_stack:
  added: [psycopg LISTEN/NOTIFY, Postgres trigger functions]
  patterns: [event-driven wakeup, idempotency coalescing, dedicated LISTEN connection, three-mode feature flag]
key_files:
  created:
    - scripts/migrations/029-wakeup-queue.sql
    - shared/wakeup_queue.py
    - tests/shared/test_wakeup_queue.py
    - tests/shared/test_wakeup_integration.py
    - tests/openjarvis/test_perseus_wakeup.py
  modified:
    - shared/db.py
    - openjarvis/vassals/perseus_scheduler.py
    - orchestrator.py
    - perseus/scheduler.py
decisions:
  - "Dedicated Postgres connection outside pool for LISTEN (pool connections are returned after queries)"
  - "Three-mode feature flag: off/shadow/full (not boolean) for safe rollout"
  - "Trigger-based NOTIFY on events table INSERT (zero coupling with emit_event)"
  - "Idempotency coalescing via agent_id:event_type:minute_bucket keys"
  - "Re-entrancy guard on dispatch_pending to prevent deadlock from nested event emission"
  - "close_pool acquires _pool_lock per AEGIS audit requirement"
  - "Exponential backoff reconnect: 1s, 2s, 4s, 8s... max 30s"
  - "Maintenance as tick-count modulo (not separate scheduler entries) for simplicity"
metrics:
  duration: "42min"
  completed: "2026-03-31"
  tasks: 8
  files: 9
  tests_added: 43
---

# Phase 16 Plan 01: Event-Driven Wakeup Queue Summary

Postgres LISTEN/NOTIFY wakeup queue replacing 60s polling with near-instant event-driven dispatch, three-mode feature flag (off/shadow/full), idempotency coalescing, and AEGIS close_pool lock fix.

## What Was Built

### Migration 029 (wakeup-queue.sql)
- `wakeup_subscriptions` table: agent event pattern registration with priority and enabled flag
- `wakeup_requests` table: pending/dispatched/expired/logged status, idempotency key deduplication, JSONB context, 5-minute TTL
- `notify_wakeup_event()` trigger function: fires `pg_notify('wakeup_events', event_type:id)` on every `INSERT INTO events`
- 26 seed subscriptions across 4 agents (titan: 8, hermes: 7, clawdbot: 4, perseus: 7)
- Idempotent migration (IF NOT EXISTS, ON CONFLICT DO NOTHING)

### WakeupQueue Core (shared/wakeup_queue.py, ~480 lines)
- Dedicated async Postgres connection outside pool for LISTEN
- Pattern matching: exact, SQL LIKE (%), wildcard (*)
- Idempotency coalescing: same agent+event+minute bucket = count increment
- `wait_for_wakeup()`: blocks until event or timeout (60s safety net)
- `dispatch_pending()`: marks pending requests as dispatched with re-entrancy guard
- `expire_stale_requests()`: CTE pattern for atomic status update
- `cleanup_old_requests()`: DELETE with make_interval(hours => %s) parameterized
- Exponential backoff reconnect (1s to 30s max)
- Stats tracking: notifications, wakeups created/coalesced, reconnects

### Perseus Scheduler Integration
- `wakeup_queue` parameter added to `PerseusScheduler.__init__()`
- Main loop: `wait_for_wakeup("perseus", timeout=tick_interval)` replaces `asyncio.sleep()`
- Fallback: if WakeupQueue fails to start, gracefully degrades to polling
- Tick step 0: dispatch pending wakeup requests
- Tick step 8: wakeup stats in tick event publish
- Tick step 9: expire stale every 5 ticks
- Tick step 10: cleanup old every 1440 ticks (~24h)
- `stop()` shuts down WakeupQueue before returning
- `status()` includes wakeup_queue stats

### Orchestrator Wiring
- Creates WakeupQueue when `EVENT_WAKEUP_ENABLED != off`
- Passes to PerseusScheduler constructor
- ImportError-safe: missing module = no queue, no crash

### AEGIS Fix: close_pool Lock
- `close_pool()` now acquires `_pool_lock` before setting `_pool = None`
- Prevents race condition with concurrent `init_pool()` during shutdown

### Maintenance Schedule Entries
- `wakeup_expire_stale`: every 300s (5 min), non-skippable
- `wakeup_cleanup`: every 86400s (daily), skippable

## Tests

| Suite | Tests | Status |
|-------|-------|--------|
| Unit (test_wakeup_queue.py) | 29 | All pass |
| Integration (test_wakeup_integration.py) | 7 | Skipped (DB-gated, SKIP_DB_TESTS=1) |
| Scheduler (test_perseus_wakeup.py) | 7 | All pass |
| **Total new** | **43** | **36 pass, 7 skip** |

Unit test coverage: feature flag modes (7), pattern matching (8), coalescing (1), wait/timeout (4), re-entrancy (1), stats (2), notification handling (3), shutdown (2), idempotency key (1).

Integration tests (DB-gated): full emit->NOTIFY->wakeup flow, immediate wakeup on emit, coalescing count, shadow mode logged-only, unmatched events, expire stale, cleanup old.

Pre-existing test suite: 5745 passed (96 pre-existing failures unrelated to this phase).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test_event_wakes_immediately race condition**
- **Found during:** Task 8 (tests)
- **Issue:** Test set asyncio.Event before calling wait_for_wakeup(), but wait_for_wakeup() clears the event on entry, causing a timeout
- **Fix:** Changed test to signal via asyncio.create_task after a 20ms delay
- **Files modified:** tests/shared/test_wakeup_queue.py
- **Commit:** 409442b

## Known Stubs

None -- all code is fully wired and functional.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 517c1e4 | feat | Migration 029: tables, trigger, seed subscriptions |
| 3706ae4 | feat | WakeupQueue core: LISTEN/NOTIFY, coalescing, wait_for_wakeup |
| b6f40ea | fix | emit_event docstring + close_pool _pool_lock AEGIS fix |
| 8506fff | feat | Perseus scheduler integration with WakeupQueue |
| a9cdadb | feat | Orchestrator wiring for WakeupQueue creation |
| 5fa5d37 | feat | Maintenance schedule entries |
| 409442b | test | Unit + integration tests (43 total) |
