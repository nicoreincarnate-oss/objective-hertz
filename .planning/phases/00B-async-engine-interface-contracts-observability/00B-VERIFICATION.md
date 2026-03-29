---
phase: 0b-async-engine-interface-contracts-observability
verified: 2026-03-29T21:30:00Z
status: passed
score: 4/4 must-haves verified
must_haves:
  truths:
    - "WorkflowEngine runs async DAGs without regression (run_async() method added)"
    - "Sync wrapper works for existing callers (run() unchanged)"
    - "shared/contracts.py has Protocol types for all 7 integration surfaces"
    - "Baseline metrics dashboard shows LLM calls/latency/errors/cost per daemon (GET /api/metrics)"
  artifacts:
    - path: "openjarvis/workflow/engine.py"
      provides: "run_async() with asyncio.gather for parallel nodes + run_sync() wrapper"
    - path: "shared/contracts.py"
      provides: "7 @runtime_checkable Protocol types"
    - path: "shared/observability.py"
      provides: "record_llm_call() and get_metrics_summary() functions"
    - path: "titan/workflow_pipeline.py"
      provides: "Async run_pipeline() using engine.run_async()"
    - path: "scripts/migrations/017-observability-tables.sql"
      provides: "llm_metrics table schema"
    - path: "hermes/web/app.py"
      provides: "GET /api/metrics endpoint"
    - path: "shared/llm_client.py"
      provides: "LLM call instrumentation via _fire_metrics()"
  key_links:
    - from: "titan/workflow_pipeline.py"
      to: "openjarvis/workflow/engine.py"
      via: "await engine.run_async()"
    - from: "shared/llm_client.py"
      to: "shared/observability.py"
      via: "_fire_metrics() calls record_llm_call()"
    - from: "hermes/web/app.py"
      to: "shared/observability.py"
      via: "get_metrics_summary() import and call in /api/metrics"
warnings:
  - file: "hermes/web/app.py"
    issue: "F811 ruff error: duplicate function name api_metrics at lines 405 and 575 (/api/metrics vs /metrics)"
    severity: warning
---

# Phase 0b: Async Engine + Interface Contracts + Observability Verification Report

**Phase Goal:** Convert WorkflowEngine to async, define integration contracts, establish metrics baseline.
**Verified:** 2026-03-29T21:30:00Z
**Status:** PASSED
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | WorkflowEngine runs async DAGs without regression | VERIFIED | `run_async()` at line 354 of engine.py uses `asyncio.gather()` for parallel nodes. 5 async tests pass (sequential, parallel, async system). Sync `run()` at line 46 is completely unchanged. |
| 2 | Sync wrapper works for existing callers | VERIFIED | `run()` method at line 46 is identical to pre-phase code (ThreadPoolExecutor path). `run_sync()` at line 452 wraps `asyncio.run(self.run_async(...))`. Backward compat test passes. |
| 3 | shared/contracts.py has Protocol types for all 7 integration surfaces | VERIFIED | 7 `@runtime_checkable` Protocol types defined: DNAProvider, SlopScorer, MemoryStore, Middleware, ContextRetriever, CryptoProvider, ThresholdProvider. 16 contract tests pass (conforming, non-conforming, runtime_checkable). |
| 4 | Baseline metrics dashboard shows LLM calls/latency/errors/cost per daemon | VERIFIED | `GET /api/metrics` at hermes/web/app.py:404 calls `get_metrics_summary()`. `record_llm_call()` in observability.py does async INSERT to llm_metrics. `_fire_metrics()` in llm_client.py instruments all `generate()` code paths (5 call sites). 6 observability tests pass. |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `openjarvis/workflow/engine.py` | run_async() + run_sync() methods | VERIFIED | 614 lines, full async execution path with asyncio.gather, inspect.isawaitable for dual sync/async system.ask() |
| `shared/contracts.py` | 7 Protocol types | VERIFIED | 151 lines, all 7 protocols with docstrings, @runtime_checkable, proper __all__ export |
| `shared/observability.py` | record_llm_call + get_metrics_summary | VERIFIED | 539 lines, LLM metrics section at line 430+, async DB insert with graceful error handling |
| `titan/workflow_pipeline.py` | async run_pipeline() | VERIFIED | 194 lines, `async def run_pipeline()` at line 144, calls `await engine.run_async()` at line 165 |
| `scripts/migrations/017-observability-tables.sql` | llm_metrics table DDL | VERIFIED | 19 lines, CREATE TABLE with all required columns, 2 indexes |
| `hermes/web/app.py` | GET /api/metrics endpoint | VERIFIED | Endpoint at line 404, imports and calls get_metrics_summary() |
| `shared/llm_client.py` | LLM call instrumentation | VERIFIED | `_fire_metrics()` method at line 63, called from all 5 code paths in `generate()` |
| `tests/openjarvis/test_async_engine.py` | Async engine tests | VERIFIED | 5 tests: sequential, async system, parallel, sync backward compat, invalid graph |
| `tests/shared/test_contracts.py` | Contract protocol tests | VERIFIED | 16 tests: 7 conforming, 7 empty rejection, partial rejection, runtime_checkable check |
| `tests/shared/test_observability.py` | Observability tests | VERIFIED | 6 tests: insert params, error type, aggregation, daemon filter, DB error handling (x2) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| titan/workflow_pipeline.py | openjarvis/workflow/engine.py | `await engine.run_async()` | WIRED | Line 165: `result = await engine.run_async(graph, _PipelineSystem(), initial_input="")` |
| shared/llm_client.py | shared/observability.py | `_fire_metrics() -> record_llm_call()` | WIRED | 5 call sites in generate(), fire-and-forget via asyncio.create_task |
| hermes/web/app.py | shared/observability.py | `get_metrics_summary()` import + call | WIRED | Import at line 48, called at line 417 in /api/metrics handler |
| observability.py | shared/db | `execute()` and `fetch_all()` | WIRED | Lazy imports inside record_llm_call (line 476) and get_metrics_summary (line 509) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| hermes /api/metrics | summary | get_metrics_summary() -> shared.db.fetch_all() | Yes -- SQL aggregation on llm_metrics table | FLOWING |
| shared/llm_client.py | metrics | _fire_metrics() -> record_llm_call() -> shared.db.execute() | Yes -- INSERT with real timing, token counts, cost | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 27 Phase 0b tests pass | `PYTHONPATH=. pytest tests/openjarvis/test_async_engine.py tests/shared/test_contracts.py tests/shared/test_observability.py -v` | 27/27 passed in 0.15s | PASS |
| Ruff clean on core files | `ruff check openjarvis/workflow/engine.py shared/contracts.py shared/observability.py titan/workflow_pipeline.py` | All checks passed | PASS |
| Protocol isinstance works | Verified via test_conforming_implementations (7 protocols) | All 7 pass isinstance check | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| ASYNC-01 | 00B-01 | Add async WorkflowEngine.run_async() | SATISFIED | run_async() at engine.py:354 with asyncio.gather for parallel nodes |
| ASYNC-02 | 00B-01 | Migrate Titan pipeline to async | SATISFIED | async run_pipeline() at workflow_pipeline.py:144, run_pipeline_sync() wrapper at line 184 |
| CONTRACT-01 | 00B-01 | Interface contracts for Phases 1-7 | SATISFIED | 7 @runtime_checkable Protocol types in shared/contracts.py |
| OBS-01 | 00B-02 | Observability baseline with metrics | SATISFIED | llm_metrics table, record_llm_call(), get_metrics_summary(), GET /api/metrics, LLM client instrumented |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| hermes/web/app.py | 575 | F811: duplicate function name `api_metrics` (shadows line 405 endpoint) | Warning | The /metrics (Prometheus) endpoint reuses the name from /api/metrics (LLM summary). Ruff flags this. Both endpoints function correctly since FastAPI routes by path, but the name collision should be fixed. |

### Human Verification Required

### 1. Async Pipeline End-to-End

**Test:** Run Titan pipeline via `await run_pipeline()` against live database with tool executors configured.
**Expected:** Pipeline executes all 10 stages via DAG, parallel stages (follow_up + sync_analytics) run concurrently, metrics recorded to llm_metrics table.
**Why human:** Requires running Postgres, configured tool executors, and daemon environment.

### 2. Metrics Dashboard in War Room

**Test:** After 1+ hour of daemon operation, visit War Room and check /api/metrics endpoint output.
**Expected:** JSON response with per-daemon breakdown: total_calls, avg_latency_ms, error_rate_pct, total_cost_usd.
**Why human:** Requires live system operation with real LLM calls generating metrics data.

### Gaps Summary

No blocking gaps found. All 4 success criteria are met with full implementation evidence. The only notable item is a ruff warning (F811 duplicate function name in hermes/web/app.py) which is cosmetic -- both endpoints work correctly. All 27 tests pass, all artifacts are substantive and wired, and data flows from LLM calls through to the metrics endpoint.

---

_Verified: 2026-03-29T21:30:00Z_
_Verifier: Claude (gsd-verifier)_
