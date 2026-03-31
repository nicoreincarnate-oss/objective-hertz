---
phase: "13"
plan: "01"
subsystem: budget-cost-patterns
tags: [budget, cost-events, middleware, aegis, paperclip]
dependency_graph:
  requires: [phase-11-budget-consolidation, phase-12-foundation-patterns]
  provides: [cost-events-table, budget-policies-table, pre-execution-gate, cost-breakdown-api]
  affects: [shared/middleware.py, tools/budget_guard.py, shared/llm_client.py, hermes/web/app.py]
tech_stack:
  added: []
  patterns: [multi-scope-budget-policies, per-call-cost-events, pre-execution-budget-gate, fail-closed-budget]
key_files:
  created:
    - scripts/migrations/026-budget-cost-patterns.sql
    - shared/cost_events.py
    - tests/shared/test_cost_events.py
    - tests/tools/test_budget_policies.py
    - tests/shared/test_budget_middleware_failclosed.py
    - tests/tools/__init__.py
  modified:
    - shared/middleware.py
    - shared/llm_client.py
    - tools/budget_guard.py
    - hermes/web/app.py
    - tests/shared/test_middleware.py
decisions:
  - "emit_event uses shared.db.emit_event (not shared.comms which lacks it)"
  - "Pre-existing hermes/web/app.py import ordering left untouched (out of scope)"
  - "Existing fail-open test updated to verify fail-closed AEGIS behavior"
metrics:
  duration: "7min"
  completed: "2026-03-31"
  tasks: 6
  files: 11
  tests_added: 37
---

# Phase 13 Plan 01: Budget & Cost Patterns Summary

Three Paperclip-derived budget infrastructure patterns: pre-execution budget gating with cost estimation, multi-scope budget policies, and per-call cost events. AEGIS fail-closed violation fixed.

## Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Migration 026 | 2c9a044 | scripts/migrations/026-budget-cost-patterns.sql |
| 2 | Per-Call Cost Events | a41da8d | shared/cost_events.py, shared/llm_client.py |
| 3 | Multi-Scope Budget Policies | cfbe450 | tools/budget_guard.py |
| 4 | Pre-Execution Budget Gate | 5003e82 | shared/middleware.py |
| 5 | Hermes Cost Breakdown | 888625a | hermes/web/app.py |
| 6 | Tests | b39de15 | 3 test files + test fix |

## What Was Built

1. **Migration 026** -- `budget_policies` table (company/agent/pipeline_stage scopes, warn_percent, hard_stop) with $800/month company seed + `cost_events` table (UUID PK, full attribution) with 4 indices.

2. **CostEvent dataclass + emit_cost_event()** -- Every LLM call emits a cost event when `PER_CALL_COST_EVENTS_ENABLED=true`. Includes `get_average_cost_by_task_type()` for pre-execution estimation and `get_agent_spend()` / `get_total_spend_current_month()` query helpers.

3. **Multi-scope policy evaluation** -- `evaluate_policies()` loads all applicable budget_policies, filters by agent/pipeline_stage context, evaluates each against spend, warns at configured percent, hard-stops at 100%. Most restrictive policy wins. Warnings emitted as events for Hermes.

4. **Pre-execution budget gate** -- `budget_check_middleware` extended with cost estimation before execution. Estimates from historical averages, checks against remaining budget, rejects with Ollama fallback signal when over. AEGIS fix: legacy path now fails CLOSED on DB errors (was fail-open).

5. **GET /api/costs/breakdown** -- Hermes dashboard endpoint with grouping by agent/model/task_type/day. Allowlist-validated group_by prevents SQL injection.

6. **37 new tests** -- 14 cost event tests, 12 policy evaluation tests, 11 middleware fail-closed tests. Existing fail-open test updated to verify AEGIS compliance.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] emit_event import path**
- **Found during:** Task 3
- **Issue:** Plan referenced `shared.comms.emit_event` but the function lives in `shared.db`
- **Fix:** Changed import to `from shared.db import emit_event`
- **Files modified:** tools/budget_guard.py, tests/tools/test_budget_policies.py
- **Commit:** b39de15

**2. [Rule 1 - Bug] Existing test assumed fail-open behavior**
- **Found during:** Task 6
- **Issue:** `test_budget_check_failure_allows_execution` tested old fail-open behavior; AEGIS fix changed to fail-closed
- **Fix:** Renamed to `test_budget_check_failure_rejects_execution`, asserts `success=False` and "fail closed" in output
- **Files modified:** tests/shared/test_middleware.py
- **Commit:** b39de15

## Feature Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `PER_CALL_COST_EVENTS_ENABLED` | false | Gates cost_events emission |
| `MULTI_SCOPE_BUDGET_ENABLED` | false | Gates multi-scope policy evaluation |
| `PRE_EXECUTION_BUDGET_GATE_ENABLED` | false | Gates pre-execution cost estimation |

All three default OFF for 48-hour shadow mode before enforcement.

## Known Stubs

None -- all code is fully wired with real data sources.

## AEGIS Compliance

- All SQL in Phase 13 files uses parameterized queries (%s placeholders)
- Zero f-string SQL in shared/cost_events.py, tools/budget_guard.py, shared/middleware.py
- Budget middleware fails CLOSED on DB errors (both legacy and pre-gate paths)
- Hermes endpoint uses allowlist for dynamic column selection (not user input)
