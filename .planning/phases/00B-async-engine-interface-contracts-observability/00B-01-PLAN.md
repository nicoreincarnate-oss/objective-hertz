---
plan: 00B-01
phase: 0b
name: Async WorkflowEngine + Interface Contracts
wave: 1
autonomous: true
req_ids: [ASYNC-01, ASYNC-02, CONTRACT-01]
objective: >
  Add async run_async() to WorkflowEngine with asyncio.gather() for parallel nodes,
  keeping sync run() unchanged. Migrate Titan pipeline to use run_async(). Create
  shared/contracts.py with Protocol types for all 7 integration surfaces (Phases 1-7).
files_modified:
  - openjarvis/workflow/engine.py
  - titan/workflow_pipeline.py
  - shared/contracts.py
task_count: 3
---

# Plan 00B-01: Async WorkflowEngine + Interface Contracts

## Objective

Add `run_async()` to WorkflowEngine, migrate Titan to use it, define interface contracts.

Read `.planning/phases/0b/PLAN.md` Tasks 1, 2, and 3 for full implementation details.

## Tasks

### Task 1: Add async WorkflowEngine.run_async() (ASYNC-01)

**File:** `openjarvis/workflow/engine.py`

Read the existing engine to understand the sync `run()` structure, then:

1. Add `async def run_async(self, graph, system, *, initial_input="", context=None)` that mirrors `run()` but:
   - Sequential nodes: `await self._execute_node_async(node, ...)`
   - Parallel nodes: `await asyncio.gather(*[self._execute_node_async(n, ...) for n in stage])`
2. Add async counterparts for each `_run_*_node()` method (check if system.ask is a coroutine)
3. Keep existing `def run()` completely unchanged — backward compat
4. Add `def run_sync()` = `asyncio.run(self.run_async(...))`
5. EventBus: if `_bus.publish()` is sync, use `asyncio.create_task()` in async path

**Commit:** `feat(0b-01): add WorkflowEngine.run_async() with asyncio parallel execution`

### Task 2: Migrate Titan pipeline to async (ASYNC-02)

**File:** `titan/workflow_pipeline.py`

1. Audit all `engine.run(` calls across codebase with grep
2. Change `run_pipeline()` to `async def run_pipeline()`
3. Replace `engine.run(graph, ...)` with `await engine.run_async(graph, ...)`
4. Update Titan daemon's call to `await run_pipeline()`
5. Any callers that can't be migrated keep `engine.run()` with a comment

**Commit:** `feat(0b-01): migrate Titan pipeline to async engine`

### Task 3: Interface contracts (CONTRACT-01)

**File:** `shared/contracts.py` (new)

Create Protocol types for all 7 integration surfaces:
- `DNAProvider` — Phase 1 (get_dna, get_token_budget)
- `SlopScorer` — Phase 2 (async score, get_threshold)
- `MemoryStore` — Phase 3 (async load, save, cleanup)
- `Middleware` — Phase 4 (async __call__ with ctx + next_fn)
- `ContextRetriever` — Phase 5 (async retrieve, store)
- `CryptoProvider` — Phase 6 (encrypt, decrypt, sign, verify)
- `ThresholdProvider` — Phase 7 (get_threshold, update)

All protocols must be `@runtime_checkable`.

See `.planning/phases/0b/PLAN.md` Task 3 for exact Protocol definitions.

Run `ruff check shared/contracts.py` — must be clean.

**Commit:** `feat(0b-01): add shared/contracts.py with Protocol types for all integration surfaces`

## Acceptance Criteria

- [ ] `run_async()` executes DAG workflows with async I/O
- [ ] `run()` still works unchanged for all existing callers
- [ ] Titan pipeline uses `await engine.run_async()`
- [ ] `shared/contracts.py` defines 7 `@runtime_checkable` Protocol types
- [ ] `ruff check` clean on all changed files
- [ ] `PYTHONPATH=. pytest tests/ -k workflow -v` passes
