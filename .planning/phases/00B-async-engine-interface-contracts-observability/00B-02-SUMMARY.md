---
plan: 00B-02
phase: 0b
status: complete
duration: ~4m
tasks_completed: 2
tasks_total: 2
commits:
  - hash: 86a3177
    message: "feat(0b-02): add observability collector + instrument llm_client + /api/metrics endpoint"
  - hash: 3b65948
    message: "test(0b-02): add Phase 0b test suite -- async engine, contracts, observability"
key_files:
  created:
    - scripts/migrations/017-observability-tables.sql
    - shared/observability.py
    - tests/openjarvis/test_async_engine.py
    - tests/shared/test_contracts.py
    - tests/shared/test_observability.py
    - tests/shared/__init__.py
  modified:
    - shared/llm_client.py
    - hermes/web/app.py
---

# Plan 00B-02 Summary: Observability Baseline + Tests

## What Was Built

1. **Migration 017** — `llm_metrics` table with daemon/model indexes for fast queries
2. **shared/observability.py** — `record_llm_call()` (fire-and-forget async INSERT) + `get_metrics_summary()` (per-daemon aggregation)
3. **LLM client instrumentation** — Every `generate()` call in `shared/llm_client.py` now records timing, tokens, cost
4. **GET /api/metrics** endpoint in Hermes web app — per-daemon summary for War Room dashboard
5. **Full Phase 0b test suite** — 27 tests across 3 files:
   - `test_async_engine.py`: async DAG execution + backward compat
   - `test_contracts.py`: all 7 Protocol types validate correctly
   - `test_observability.py`: record/summary/error handling

## Test Results

52/52 tests passing (25 Phase 0a + 27 Phase 0b).

## Deviations

None.
