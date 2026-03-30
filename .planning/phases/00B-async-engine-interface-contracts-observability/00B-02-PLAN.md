---
plan: 00B-02
phase: 0b
name: Observability Baseline + Tests
wave: 2
autonomous: true
req_ids: [OBS-01]
objective: >
  Create llm_metrics table (migration 017), shared/observability.py metrics collector,
  instrument shared/llm_client.py to record every LLM call, add GET /api/metrics to
  hermes web app. Write tests for async engine, contracts, and observability.
files_modified:
  - scripts/migrations/017-observability-tables.sql
  - shared/observability.py
  - shared/llm_client.py
  - hermes/web/app.py
  - tests/openjarvis/test_async_engine.py
  - tests/shared/test_contracts.py
  - tests/shared/test_observability.py
task_count: 2
---

# Plan 00B-02: Observability Baseline + Tests

## Objective

LLM metrics collection + observability dashboard endpoint + full test suite for Phase 0b.

Read `.planning/phases/0b/PLAN.md` Tasks 4 and 5 for full implementation details.

## Tasks

### Task 1: Observability baseline (OBS-01)

**Files:** `scripts/migrations/017-observability-tables.sql`, `shared/observability.py`, `shared/llm_client.py`, `hermes/web/app.py`

#### 1a. Migration 017
Create `scripts/migrations/017-observability-tables.sql`:
```sql
CREATE TABLE IF NOT EXISTS llm_metrics (
    id SERIAL PRIMARY KEY,
    daemon TEXT NOT NULL,
    model TEXT NOT NULL,
    call_type TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    cost_usd DECIMAL(10,6),
    success BOOLEAN DEFAULT TRUE,
    error_type TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_llm_metrics_daemon ON llm_metrics (daemon, created_at DESC);
CREATE INDEX idx_llm_metrics_model ON llm_metrics (model, created_at DESC);
```

**Commit:** `feat(0b-02): add migration 017 — llm_metrics observability table`

#### 1b. Metrics collector
Create `shared/observability.py`:
- `async def record_llm_call(daemon, model, call_type, input_tokens, output_tokens, latency_ms, cost_usd, success=True, error_type=None)` — fire-and-forget async INSERT to llm_metrics (use asyncio.create_task to avoid blocking)
- `async def get_metrics_summary(daemon=None, hours=24)` — aggregate: total calls, avg latency, error rate, total cost per daemon
- Handle DB unavailable gracefully (log warning, don't raise)

#### 1c. Instrument llm_client.py
In `shared/llm_client.py` `generate()` method:
- Wrap with `time.perf_counter()` timing
- After call: `asyncio.create_task(record_llm_call(...))` — fire and forget
- Extract input/output tokens from response where available
- Cost estimation: `input_tokens * 0.000003 + output_tokens * 0.000015` (sonnet rates as default)

#### 1d. Hermes metrics endpoint
In `hermes/web/app.py`, add:
```python
@app.get("/api/metrics")
async def get_metrics():
    summary = await get_metrics_summary()
    return summary
```

**Commit:** `feat(0b-02): add observability collector + instrument llm_client + /api/metrics endpoint`

### Task 2: Phase 0b test suite

**Files:** `tests/openjarvis/test_async_engine.py`, `tests/shared/test_contracts.py`, `tests/shared/test_observability.py`

Also create `tests/shared/__init__.py` if it doesn't exist.

#### 2a. Async engine tests (`test_async_engine.py`)
- `test_run_async_sequential_dag`: 3 nodes in sequence, verify execution order
- `test_run_async_parallel_nodes`: 2 parallel nodes, verify both execute
- `test_run_backward_compat`: existing `run()` method still works for simple DAG
- Mock `system.ask()` as AsyncMock

#### 2b. Contracts tests (`test_contracts.py`)
For each of the 7 Protocol types:
- Create a minimal class implementing the protocol
- `assert isinstance(impl, ProtocolType)` — verifies @runtime_checkable works
- Test non-conforming class returns False

#### 2c. Observability tests (`test_observability.py`)
- `test_record_llm_call`: mock DB execute, verify correct INSERT params
- `test_get_metrics_summary`: mock DB fetch, verify aggregation returned
- `test_record_handles_db_error`: DB raises, function logs warning and doesn't raise

Run:
```bash
PYTHONPATH=. pytest tests/openjarvis/test_async_engine.py tests/shared/test_contracts.py tests/shared/test_observability.py -v --tb=short
```

All tests must pass.

**Commit:** `test(0b-02): add Phase 0b test suite — async engine, contracts, observability`

## Acceptance Criteria

- [ ] `llm_metrics` table created via migration 017
- [ ] Every LLM call records metrics (daemon, model, latency, tokens, cost)
- [ ] `GET /api/metrics` returns per-daemon summary
- [ ] All 3 test files pass
- [ ] `ruff check shared/observability.py` clean
