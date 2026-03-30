# Phase 0b: Async Engine + Interface Contracts + Observability

**Goal:** Convert WorkflowEngine to async, define integration contracts, establish metrics baseline.
**Requirements:** ASYNC-01, ASYNC-02, CONTRACT-01, OBS-01
**Depends on:** None (can run in parallel with Phase 0a)
**Feature flag:** N/A (infrastructure)

---

## Context

The WorkflowEngine (`openjarvis/workflow/engine.py:39`) uses sync `def run()` with `concurrent.futures.ThreadPoolExecutor` for parallel nodes. This blocks the event loop and prevents Phases 3-5 from using async middleware, memory I/O, and LLM calls efficiently. The engine must become async while preserving backward compatibility.

Interface contracts (`shared/contracts.py`) prevent a Phase 4 integration cliff by defining Protocol types upfront for all integration surfaces (DNA, anti-slop, memory, middleware).

### Current State

| Component | File | Status |
|-----------|------|--------|
| WorkflowEngine.run() | `openjarvis/workflow/engine.py:39-137` | Sync, uses ThreadPoolExecutor |
| Main caller | `titan/workflow_pipeline.py:164` | `engine.run(graph, system, initial_input="")` |
| contracts.py | `shared/contracts.py` | Does not exist |
| Observability | None | No structured metrics collection |

---

## Tasks

### Task 1: Convert WorkflowEngine to async (ASYNC-01)
**File:** `openjarvis/workflow/engine.py`
**What:**

#### 1a. Add `async def run_async()` method
- New method `async def run_async(graph, system, *, initial_input, context)` that mirrors `run()` but uses `asyncio.gather()` for parallel nodes instead of ThreadPoolExecutor
- Sequential nodes: `await self._execute_node_async(node, ...)`
- Parallel nodes: `await asyncio.gather(*[self._execute_node_async(n, ...) for n in stage])`
- All `_run_*_node()` methods get async counterparts: `_run_agent_node_async()`, `_run_tool_node_async()`, etc.
- The `system.ask()` call in `_run_agent_node` must support both sync and async (check for coroutine)

#### 1b. Add sync wrapper preserving backward compatibility
- Keep existing `def run()` method unchanged — it continues to work for all existing callers
- Add `def run_sync()` convenience wrapper that calls `asyncio.run(self.run_async(...))` for callers that want async internally but have a sync entry point
- Deprecation notice in docstring for `run()` pointing to `run_async()`

#### 1c. EventBus async support
- Check if `self._bus.publish()` is sync — if so, make it fire-and-forget with `asyncio.create_task()` in async path
- Alternatively, add `async def publish_async()` to EventBus if needed

**Acceptance criteria:**
- [ ] `run_async()` executes DAG workflows with async I/O
- [ ] `run()` still works unchanged for existing callers (backward compatible)
- [ ] Parallel nodes use `asyncio.gather()` instead of ThreadPoolExecutor
- [ ] All existing engine tests pass without modification

### Task 2: Migrate call sites (ASYNC-02)
**File:** `titan/workflow_pipeline.py` (primary), plus any other callers
**What:**

#### 2a. Audit all callers
- `titan/workflow_pipeline.py:164` — `engine.run(graph, _PipelineSystem(), initial_input="")`
- Search for any other `engine.run(` calls across the codebase
- Search for `WorkflowEngine(` instantiations

#### 2b. Migrate Titan pipeline to async
- Change `titan/workflow_pipeline.py:run_pipeline()` from sync to `async def run_pipeline()`
- Replace `engine.run(graph, ...)` with `await engine.run_async(graph, ...)`
- Update Titan daemon's call to `run_pipeline()` to use `await`

#### 2c. Keep sync fallback
- Any caller that can't be migrated gets `engine.run()` (unchanged sync path)
- Document which callers are async vs sync in a comment block

**Acceptance criteria:**
- [ ] Titan pipeline runs via `run_async()`
- [ ] No other callers are broken
- [ ] `PYTHONPATH=. pytest tests/ -k workflow -v` passes

### Task 3: Interface contracts (CONTRACT-01)
**File:** `shared/contracts.py` (new)
**What:**

Define Python Protocol types for all integration surfaces that Phases 1-7 will implement:

```python
from typing import Protocol, Any, runtime_checkable

@runtime_checkable
class DNAProvider(Protocol):
    """Phase 1: Provides DNA context for LLM calls."""
    def get_dna(self, daemon_name: str) -> str: ...
    def get_token_budget(self) -> int: ...

@runtime_checkable
class SlopScorer(Protocol):
    """Phase 2: Scores content quality across 5 dimensions."""
    async def score(self, content: str, context: str) -> dict[str, float]: ...
    def get_threshold(self, context: str) -> float: ...

@runtime_checkable
class MemoryStore(Protocol):
    """Phase 3: Persistent daemon memory."""
    async def load(self, daemon_name: str, memory_type: str) -> list[dict]: ...
    async def save(self, daemon_name: str, memory_type: str, entry: dict) -> None: ...
    async def cleanup(self, daemon_name: str) -> int: ...

@runtime_checkable
class Middleware(Protocol):
    """Phase 4: Async middleware for pipeline stages."""
    async def __call__(self, ctx: dict[str, Any], next_fn: Any) -> Any: ...

@runtime_checkable
class ContextRetriever(Protocol):
    """Phase 5: RLM recursive context retrieval."""
    async def retrieve(self, query: str, limit: int) -> list[dict]: ...
    async def store(self, content: str, metadata: dict) -> str: ...

@runtime_checkable
class CryptoProvider(Protocol):
    """Phase 6: Encryption and signing."""
    def encrypt(self, plaintext: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...
    def sign(self, message: bytes) -> bytes: ...
    def verify(self, message: bytes, signature: bytes) -> bool: ...

@runtime_checkable
class ThresholdProvider(Protocol):
    """Phase 7: Adaptive threshold management."""
    def get_threshold(self, name: str) -> float: ...
    def update(self, name: str, outcome: float) -> None: ...
```

Each Protocol is `@runtime_checkable` so implementations can be validated at startup.

**Acceptance criteria:**
- [ ] `shared/contracts.py` defines Protocol types for all 7 integration surfaces
- [ ] All protocols are `@runtime_checkable`
- [ ] `ruff check shared/contracts.py` clean
- [ ] Type stubs work: `isinstance(my_scorer, SlopScorer)` returns True for conforming implementations

### Task 4: Observability baseline (OBS-01)
**Files:**
- `shared/observability.py` (new) — Metrics collection
- `scripts/migrations/017-observability-tables.sql` (new) — Metrics table

**What:**

#### 4a. Metrics table
```sql
CREATE TABLE IF NOT EXISTS llm_metrics (
    id SERIAL PRIMARY KEY,
    daemon TEXT NOT NULL,
    model TEXT NOT NULL,
    call_type TEXT NOT NULL,  -- 'generate', 'embed', etc.
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

#### 4b. Metrics collector (`shared/observability.py`)
- `record_llm_call(daemon, model, call_type, input_tokens, output_tokens, latency_ms, cost_usd, success, error_type)` — async INSERT to llm_metrics
- `get_metrics_summary(daemon=None, hours=24)` — aggregate stats: total calls, avg latency, error rate, total cost
- Integrate into `shared/llm_client.py` `generate()` method — wrap each call with timing + token counting + cost estimation

#### 4c. Dashboard query
- Add metrics summary endpoint to `hermes/web/app.py` — `GET /api/metrics` returns JSON summary per daemon
- This feeds the War Room dashboard

**Acceptance criteria:**
- [ ] `llm_metrics` table created via migration
- [ ] Every LLM call records metrics (daemon, model, latency, tokens, cost)
- [ ] `GET /api/metrics` returns per-daemon summary (calls, latency, errors, cost)
- [ ] Baseline visible in War Room after 1 hour of operation

### Task 5: Tests
**Files:**
- `tests/openjarvis/test_async_engine.py` (new)
- `tests/shared/test_contracts.py` (new)
- `tests/shared/test_observability.py` (new)

**What:**
- Test `run_async()` executes a simple 3-node DAG correctly
- Test `run()` backward compatibility (existing sync behavior preserved)
- Test all Protocol types validate correctly against mock implementations
- Test `record_llm_call()` inserts a row, `get_metrics_summary()` aggregates

**Acceptance criteria:**
- [ ] `PYTHONPATH=. pytest tests/openjarvis/test_async_engine.py tests/shared/test_contracts.py tests/shared/test_observability.py -v` passes
- [ ] `ruff check openjarvis/workflow/engine.py shared/contracts.py shared/observability.py` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] WorkflowEngine runs async DAGs without regression
- [ ] Sync wrapper works for existing callers
- [ ] `shared/contracts.py` has Protocol types for DNAProvider, SlopScorer, MemoryStore, Middleware
- [ ] Baseline metrics dashboard shows LLM calls/latency/errors/cost per daemon

## Dependencies on Later Phases

- Phase 1: Implements `DNAProvider` contract
- Phase 2: Implements `SlopScorer` contract
- Phase 3: Implements `MemoryStore` contract, uses async engine
- Phase 4: Implements `Middleware` contract, uses async engine
- Phase 5: Implements `ContextRetriever` contract
- Phase 6: Implements `CryptoProvider` contract
- Phase 7: Implements `ThresholdProvider` contract

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Async conversion breaks existing pipeline | Medium | High | Keep sync `run()` untouched, add `run_async()` as new method |
| Protocol types don't match final implementation | Medium | Low | Protocols are easy to evolve; runtime_checkable catches mismatches early |
| Metrics overhead slows pipeline | Low | Medium | Async fire-and-forget INSERT, batch if needed |

---

*Plan created: 2026-03-29*
