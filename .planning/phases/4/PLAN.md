# Phase 4: DeerFlow Async Middleware Chain

**Goal:** Cross-cutting middleware pipeline for all Titan stages. Memory, DNA, anti-slop, and telemetry as composable layers.
**Requirements:** MW-01, MW-02, MW-03, MW-04, MW-05, MW-06, MW-07, MW-08
**Depends on:** Phase 0b (async engine), Phase 1 (DNA), Phase 2 (anti-slop), Phase 3 (memory)
**Feature flag:** `ENABLE_MIDDLEWARE`

---

## Context

Phases 1-3 built DNA, anti-slop, and memory as standalone components. Phase 4 composes them into an ordered middleware chain that wraps every Titan pipeline stage. This is the integration phase — the "glue" that turns independent modules into a coherent system.

The async WorkflowEngine (Phase 0b) already supports DAG execution. Middleware wraps each node's execution with pre/post hooks. The `Middleware` Protocol (Phase 0b) defines the interface.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Titan pipeline | `titan/workflow_pipeline.py` | 10 stages via WorkflowEngine |
| Pipeline stages | `titan/pipeline/*.py` | Individual stage modules |
| WorkflowEngine | `openjarvis/workflow/engine.py` | Async after Phase 0b |
| Middleware Protocol | `shared/contracts.py` | Defined in Phase 0b |
| stage_metrics table | None | Does not exist |

---

## Tasks

### Task 1: Async middleware protocol implementation (MW-01)
**File:** `shared/middleware.py` (new)
**What:**

```python
from typing import Any, Callable, Awaitable

StageResult = dict[str, Any]
NextFn = Callable[[dict[str, Any]], Awaitable[StageResult]]

class MiddlewareChain:
    """Ordered chain of async middleware functions."""

    def __init__(self):
        self._middlewares: list[Callable] = []

    def use(self, middleware: Callable) -> "MiddlewareChain":
        """Add middleware to the chain. Order matters."""
        self._middlewares.append(middleware)
        return self

    async def execute(self, ctx: dict[str, Any], handler: NextFn) -> StageResult:
        """Execute middleware chain around the handler."""
        async def compose(index: int, ctx: dict) -> StageResult:
            if index >= len(self._middlewares):
                return await handler(ctx)
            mw = self._middlewares[index]
            return await mw(ctx, lambda c: compose(index + 1, c))

        return await compose(0, ctx)
```

**Acceptance criteria:**
- [ ] `async def middleware(ctx, next) -> StageResult` pattern works
- [ ] Middleware executes in order (first added = outermost)
- [ ] Each middleware can modify ctx before and after `next()`

### Task 2: Per-pipeline middleware config (MW-02)
**File:** `shared/middleware.py` (extend)
**What:**

```python
# Default middleware stack for Titan pipeline
TITAN_MIDDLEWARE = [
    "budget_check",    # First: reject if over budget
    "dna_guard",       # Second: validate action against DNA boundaries
    "anti_slop",       # Third: quality gate on content-producing stages
    "memory",          # Fourth: inject relevant memories, save outcomes
    "telemetry",       # Last: log timing, tokens, cost
]

# Configurable per pipeline
PIPELINE_CONFIGS = {
    "titan": TITAN_MIDDLEWARE,
    "clawdbot": ["budget_check", "dna_guard", "anti_slop", "telemetry"],
    "hermes": ["budget_check", "dna_guard", "telemetry"],
    "perseus": ["budget_check", "telemetry"],
}

def build_chain(pipeline_name: str) -> MiddlewareChain:
    """Build middleware chain for a specific pipeline."""
    chain = MiddlewareChain()
    config = PIPELINE_CONFIGS.get(pipeline_name, ["telemetry"])
    for mw_name in config:
        chain.use(MIDDLEWARE_REGISTRY[mw_name])
    return chain
```

**Acceptance criteria:**
- [ ] Each pipeline has configurable middleware stack
- [ ] Middleware ordering is explicit and documented
- [ ] Default stack: budget → DNA → anti-slop → memory → telemetry

### Task 3: MemoryMiddleware (MW-03)
**File:** `shared/middleware.py` (extend)
**What:**

```python
async def memory_middleware(ctx: dict, next_fn: NextFn) -> StageResult:
    """Read relevant memories before stage, write outcome after."""
    if not feature_flag("ENABLE_DEERFLOW_MEMORY"):
        return await next_fn(ctx)

    daemon = ctx.get("daemon_name", "")
    stage = ctx.get("stage_name", "")

    # Pre-stage: inject relevant memories
    store = DaemonMemoryStore()
    memories = await store.load(daemon, "episodic")
    ctx["memories"] = memories

    # Execute stage
    result = await next_fn(ctx)

    # Post-stage: save outcome as episodic memory
    if result.get("success"):
        await store.save_episodic(daemon, f"{stage}_outcome", {
            "stage": stage,
            "result_summary": str(result.get("output", ""))[:500],
            "timestamp": datetime.utcnow().isoformat(),
        })

    return result
```

**Acceptance criteria:**
- [ ] Memories injected into stage context before execution
- [ ] Stage outcomes saved as episodic memories after execution
- [ ] Feature flag gates memory middleware

### Task 4: DNAGuardMiddleware (MW-04)
**File:** `shared/middleware.py` (extend)
**What:**

```python
async def dna_guard_middleware(ctx: dict, next_fn: NextFn) -> StageResult:
    """Validate stage actions against agent DNA boundaries."""
    if not feature_flag("ENABLE_DNA_PROFILES"):
        return await next_fn(ctx)

    daemon = ctx.get("daemon_name", "")
    stage = ctx.get("stage_name", "")
    tools_used = ctx.get("tools", [])

    dna = AgentDNA(daemon)
    dna.load()

    # Check tool permissions
    for tool in tools_used:
        if not dna.check_action(stage, tool):
            logger.warning(f"DNA GUARD: {daemon} blocked from using {tool} in {stage}")
            return {"success": False, "output": f"DNA boundary violation: {tool} not permitted"}

    return await next_fn(ctx)
```

**Acceptance criteria:**
- [ ] DNA Guard blocks unauthorized tool usage
- [ ] Violations logged and returned as stage failure
- [ ] Feature flag gates DNA guard

### Task 5: AntiSlopMiddleware (MW-05)
**File:** `shared/middleware.py` (extend)
**What:**

```python
async def anti_slop_middleware(ctx: dict, next_fn: NextFn) -> StageResult:
    """Wrap content-producing stages with quality scoring."""
    if not feature_flag("ENABLE_ANTI_SLOP"):
        return await next_fn(ctx)

    # Only apply to content-producing stages
    CONTENT_STAGES = {"email_compose", "site_build", "alert_compose"}
    if ctx.get("stage_name") not in CONTENT_STAGES:
        return await next_fn(ctx)

    result = await next_fn(ctx)

    if result.get("success") and result.get("output"):
        scorer = AntiSlopScorer()
        scores = await scorer.score(result["output"], ctx.get("stage_name", "email"))
        result["quality_scores"] = scores

        # Secret detection
        secrets = detect_secrets(result["output"])
        if secrets:
            logger.critical(f"SECRET DETECTED in {ctx.get('stage_name')}: {secrets}")
            return {"success": False, "output": "BLOCKED: secret detected in output"}

    return result
```

**Acceptance criteria:**
- [ ] Content-producing stages automatically quality-scored
- [ ] Secret detection blocks output
- [ ] Non-content stages pass through unaffected

### Task 6: TelemetryMiddleware + stage_metrics table (MW-06, MW-07)
**Files:**
- `shared/middleware.py` (extend)
- `scripts/migrations/020-stage-metrics.sql` (new)

**What:**

```sql
CREATE TABLE IF NOT EXISTS stage_metrics (
    id SERIAL PRIMARY KEY,
    pipeline TEXT NOT NULL,
    stage TEXT NOT NULL,
    daemon TEXT NOT NULL,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd DECIMAL(10,6),
    success BOOLEAN DEFAULT TRUE,
    middleware_overhead_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stage_metrics_pipeline ON stage_metrics (pipeline, created_at DESC);
CREATE INDEX idx_stage_metrics_stage ON stage_metrics (stage, created_at DESC);
```

```python
async def telemetry_middleware(ctx: dict, next_fn: NextFn) -> StageResult:
    """Log stage timing, token usage, cost to stage_metrics table."""
    t0 = time.time()
    result = await next_fn(ctx)
    duration_ms = int((time.time() - t0) * 1000)

    await execute(
        """INSERT INTO stage_metrics (pipeline, stage, daemon, duration_ms, success)
           VALUES (%s, %s, %s, %s, %s)""",
        (ctx.get("pipeline", ""), ctx.get("stage_name", ""),
         ctx.get("daemon_name", ""), duration_ms, result.get("success", False)),
    )
    return result
```

**Acceptance criteria:**
- [ ] Every stage execution logged to `stage_metrics`
- [ ] Timing, tokens, and cost captured
- [ ] Middleware overhead tracked separately

### Task 7: Middleware ordering config (MW-08)
**File:** `shared/middleware.py` (ensure ordering is configurable)
**What:**
- Ordering defined in `PIPELINE_CONFIGS` dict (Task 2)
- Override via environment variable: `TITAN_MIDDLEWARE_ORDER=budget,dna,telemetry`
- Validation: warn if anti-slop comes before DNA (DNA should filter first)

**Acceptance criteria:**
- [ ] Middleware ordering configurable via config dict
- [ ] Environment variable override works
- [ ] Invalid ordering logged as warning

### Task 8: Integration into WorkflowEngine (MW-01 wiring)
**File:** `openjarvis/workflow/engine.py` (modify `_execute_node_async`)
**What:**

Wrap each node execution with middleware chain:

```python
async def _execute_node_async(self, node, outputs, ctx, system, graph):
    if feature_flag("ENABLE_MIDDLEWARE"):
        chain = build_chain(ctx.get("pipeline", "titan"))
        stage_ctx = {
            "daemon_name": ctx.get("daemon_name", ""),
            "stage_name": node.id,
            "pipeline": ctx.get("pipeline", ""),
            "tools": node.tools or [],
        }
        result = await chain.execute(stage_ctx, lambda c: self._run_node_async(node, outputs, c, system, graph))
    else:
        result = await self._run_node_async(node, outputs, ctx, system, graph)
```

**Acceptance criteria:**
- [ ] Middleware chain executes on every Titan pipeline stage
- [ ] Middleware stack adds < 200ms per stage
- [ ] No pipeline regression (all existing tests pass)
- [ ] Feature flag off = no middleware, existing behavior

### Task 9: Tests
**Files:** `tests/shared/test_middleware.py` (new)
**What:**
- Test middleware chain execution order
- Test MemoryMiddleware injects and saves
- Test DNAGuard blocks unauthorized tools
- Test AntiSlop scores content stages, passes non-content
- Test TelemetryMiddleware records to stage_metrics
- Test configurable ordering
- Test full chain: budget → DNA → anti-slop → memory → telemetry

**Acceptance criteria:**
- [ ] All tests pass
- [ ] `ruff check shared/middleware.py` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] Middleware chain executes on every Titan pipeline stage
- [ ] Middleware ordering configurable (budget → DNA → anti-slop → memory → telemetry)
- [ ] `stage_metrics` table populated with timing and cost data
- [ ] No pipeline regression (all existing tests pass)
- [ ] Middleware stack adds < 200ms per stage

---

*Plan created: 2026-03-29*
