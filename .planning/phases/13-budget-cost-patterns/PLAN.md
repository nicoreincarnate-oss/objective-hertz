# Phase 13: Budget & Cost Patterns

**Goal:** Implement 3 Paperclip-derived budget infrastructure patterns into Objective Hertz: pre-execution budget gating with cost estimation, multi-scope budget policies, and per-call cost events. Fixes the existing AEGIS violation where budget_check_middleware fails OPEN on DB errors.
**Requirements:** BUDGET-01 through BUDGET-03 (Paperclip patterns 6, 7, 8)
**Depends on:** Phase 11 (budget consolidation — single budget_check_middleware), Phase 12 (agent state machines)
**Feature flags:** `PRE_EXECUTION_BUDGET_GATE_ENABLED`, `MULTI_SCOPE_BUDGET_ENABLED`, `PER_CALL_COST_EVENTS_ENABLED`
**License:** MIT (Paperclip source)

---

## Context

After Phase 11, all budget checking lives in ONE place: `shared/middleware.py:budget_check_middleware`. It queries `get_metrics_summary(hours=720)` to sum LLM cost across the `llm_metrics` table and hard-blocks at $800/month. Cost recording lives in two places:

1. `shared/llm_client.py:_record_claude_spend()` — estimates cost and writes to `budget_tracking` table (month, category, amount, description, client_id, pipeline_stage)
2. `shared/observability.py:record_llm_call()` — writes to `llm_metrics` table (daemon, model, call_type, input_tokens, output_tokens, latency_ms, cost_usd, success, error_type)

The budget_check_middleware currently has an AEGIS violation: on DB errors it **allows** the call through (`logger.warning("Budget check failed (allowing): %s", exc)`). AEGIS requires fail CLOSED.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Budget middleware | `shared/middleware.py` | budget_check_middleware at $800 cap |
| BudgetGuard class | `tools/budget_guard.py` | check_budget(), can_spend(), get_month_spending() |
| Cost recording | `shared/llm_client.py` | _record_claude_spend() to budget_tracking |
| LLM metrics | `shared/observability.py` | record_llm_call() to llm_metrics |
| Budget tracking table | `scripts/init-db.sql` | month, category, amount, description, client_id, pipeline_stage |
| LLM metrics table | `scripts/migrations/017-observability-tables.sql` | daemon, model, call_type, tokens, cost_usd, latency_ms |
| Hermes budget API | `hermes/web/app.py` | GET /api/budget |
| Feature flags | `shared/middleware.py:_flag()` | env-based, defaults TRUE |
| Observability context | `shared/observability.py` | _task_id ContextVar via set_observability_context() |
| Company budget | config | $800/month |

---

## Tasks

### Task 1: Database Migration 026 (BUDGET-01, BUDGET-02, BUDGET-03)
**File:** `scripts/migrations/026-budget-cost-patterns.sql` (new)
**What:**

Create the two new tables and seed the default company policy.

```sql
-- Migration 026: Budget & Cost Patterns (Paperclip patterns 6, 7, 8)
-- Phase 13: Pre-execution budget gate, multi-scope policies, per-call cost events

-- =====================================================================
-- Table 1: budget_policies (Pattern 7 — Multi-Scope Budget Policies)
-- =====================================================================
CREATE TABLE IF NOT EXISTS budget_policies (
    policy_id SERIAL PRIMARY KEY,
    scope_type VARCHAR(50) NOT NULL CHECK (scope_type IN ('company', 'agent', 'pipeline_stage')),
    scope_value VARCHAR(200) NOT NULL,
    window_kind VARCHAR(20) NOT NULL CHECK (window_kind IN ('monthly', 'lifetime')),
    limit_usd DECIMAL(10,2) NOT NULL,
    warn_percent INTEGER NOT NULL DEFAULT 80 CHECK (warn_percent BETWEEN 1 AND 99),
    hard_stop_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(scope_type, scope_value, window_kind)
);

-- Seed default company policy: $800/month, warn at 80%, hard stop enabled
INSERT INTO budget_policies (scope_type, scope_value, window_kind, limit_usd, warn_percent, hard_stop_enabled, description)
VALUES ('company', 'objective_hertz', 'monthly', 800.00, 80, TRUE, 'Company-wide monthly budget cap')
ON CONFLICT (scope_type, scope_value, window_kind) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_budget_policies_scope ON budget_policies(scope_type, scope_value);

-- =====================================================================
-- Table 2: cost_events (Pattern 8 — Per-Call Cost Events)
-- =====================================================================
CREATE TABLE IF NOT EXISTS cost_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(100) NOT NULL,
    task_id VARCHAR(200),
    task_type VARCHAR(100),
    model VARCHAR(100) NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DECIMAL(10,6) NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cost_events_agent_created ON cost_events(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_events_task ON cost_events(task_id) WHERE task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cost_events_created ON cost_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_events_model ON cost_events(model, created_at DESC);
```

**Acceptance criteria:**
- [ ] Migration runs cleanly on fresh DB and is idempotent
- [ ] budget_policies table has default company policy ($800/month)
- [ ] cost_events table has UUID primary key and all required columns
- [ ] All indices created
- [ ] Parameterized SQL only (no f-strings, no concatenation)

---

### Task 2: Per-Call Cost Events (BUDGET-03 — Pattern 8)
**Files:**
- `shared/cost_events.py` (new)
- `shared/llm_client.py` (modify)
- `shared/observability.py` (modify)

**Why first:** Cost events are the data layer that patterns 6 and 7 query. Build the data source before the consumers.

**File: `shared/cost_events.py` (new)**

```python
"""Per-call cost event recording — Paperclip Pattern 8.

Every LLM call emits a CostEvent to the cost_events table.
Includes task_id from observability context for attribution.
Feature flag: PER_CALL_COST_EVENTS_ENABLED (env-based, default false).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("perseus.cost_events")


def _cost_events_enabled() -> bool:
    return os.environ.get("PER_CALL_COST_EVENTS_ENABLED", "false").lower() in ("true", "1", "yes")


@dataclass
class CostEvent:
    """Single LLM call cost record."""
    agent_id: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    task_id: str | None = None
    task_type: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)


async def emit_cost_event(event: CostEvent) -> None:
    """Write a CostEvent to the cost_events table.

    Fire-and-forget: logs warning on failure, never raises.
    Respects PER_CALL_COST_EVENTS_ENABLED feature flag.
    """
    if not _cost_events_enabled():
        return

    try:
        from shared.db import execute

        await execute(
            """INSERT INTO cost_events
               (event_id, agent_id, task_id, task_type, model,
                tokens_in, tokens_out, cached_tokens, cost_usd, latency_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                event.event_id,
                event.agent_id,
                event.task_id,
                event.task_type,
                event.model,
                event.tokens_in,
                event.tokens_out,
                event.cached_tokens,
                round(event.cost_usd, 6),
                event.latency_ms,
            ),
        )
    except Exception as exc:
        logger.warning("Cost event emission failed (non-fatal): %s", exc)


async def get_agent_spend(agent_id: str, window_sql: str = "30 days") -> float:
    """Sum cost_usd for an agent within a time window.

    Returns 0.0 on error (fail safe for reads — writes are what must fail closed).
    """
    try:
        from shared.db import fetch_val

        total = await fetch_val(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events "
            "WHERE agent_id = %s AND created_at >= NOW() - INTERVAL %s",
            (agent_id, window_sql),
        )
        return float(total or 0)
    except Exception as exc:
        logger.warning("Agent spend query failed: %s", exc)
        return 0.0


async def get_total_spend_current_month() -> float:
    """Sum cost_usd across all agents for the current calendar month.

    Returns 0.0 on error.
    """
    try:
        from shared.db import fetch_val

        total = await fetch_val(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events "
            "WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)"
        )
        return float(total or 0)
    except Exception as exc:
        logger.warning("Monthly spend query failed: %s", exc)
        return 0.0


async def get_average_cost_by_task_type(task_type: str, lookback_days: int = 30) -> float | None:
    """Lookup average cost for a task_type from historical cost_events.

    Used by Pattern 6 (pre-execution budget gate) for cost estimation.
    Returns None if no data exists (caller decides fallback).
    """
    try:
        from shared.db import fetch_val

        avg = await fetch_val(
            "SELECT AVG(cost_usd) FROM cost_events "
            "WHERE task_type = %s AND created_at >= NOW() - INTERVAL %s",
            (task_type, f"{lookback_days} days"),
        )
        return float(avg) if avg is not None else None
    except Exception as exc:
        logger.warning("Average cost lookup failed: %s", exc)
        return None
```

**File: `shared/llm_client.py` (modify `_fire_metrics`)**

Extend `_fire_metrics` to also emit a CostEvent alongside the existing `record_llm_call`:

```python
# In _fire_metrics(), after the existing record_llm_call task creation:
# Add cost event emission

from shared.cost_events import CostEvent, emit_cost_event
from shared.observability import _task_id as _obs_task_id

cost_event = CostEvent(
    agent_id=daemon,
    model=model,
    tokens_in=input_tokens,
    tokens_out=output_tokens,
    cached_tokens=0,  # Anthropic API returns this; wire when SDK provides it
    cost_usd=cost_usd,
    latency_ms=latency_ms,
    task_id=_obs_task_id.get() or None,
    task_type=call_type,
)
try:
    loop.create_task(emit_cost_event(cost_event))
except RuntimeError:
    pass
```

The existing `_record_claude_spend` and `record_llm_call` remain untouched (backward compatibility). `cost_events` is the richer replacement that will eventually supersede both, but during shadow mode all three coexist.

**File: `shared/observability.py` (no changes required)**

The `_task_id` ContextVar is already public enough (module-level) for `_fire_metrics` to import. No changes to observability needed.

**Acceptance criteria:**
- [ ] `CostEvent` dataclass captures all fields from spec (agent_id, task_id, task_type, model, tokens_in, tokens_out, cached_tokens, cost_usd, latency_ms)
- [ ] `emit_cost_event()` writes to cost_events table with parameterized SQL
- [ ] Feature flag `PER_CALL_COST_EVENTS_ENABLED` gates emission (default false)
- [ ] `get_average_cost_by_task_type()` returns historical average for Pattern 6
- [ ] `_fire_metrics` in llm_client.py emits CostEvent alongside existing metrics
- [ ] Existing `_record_claude_spend` and `record_llm_call` unchanged (coexist)
- [ ] task_id pulled from observability ContextVar automatically

---

### Task 3: Multi-Scope Budget Policies (BUDGET-02 — Pattern 7)
**Files:**
- `tools/budget_guard.py` (modify)
- `shared/cost_events.py` (used by queries)

**What:**

Extend `BudgetGuard` to evaluate budget_policies from the database. Most restrictive policy wins.

**Add to `tools/budget_guard.py`:**

```python
@dataclass
class PolicyResult:
    """Result of evaluating a single budget policy."""
    policy_id: int
    scope_type: str       # 'company' | 'agent' | 'pipeline_stage'
    scope_value: str
    window_kind: str      # 'monthly' | 'lifetime'
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    percent_used: float
    warn_triggered: bool  # True when percent_used >= warn_percent
    hard_stop: bool       # True when percent_used >= 100 AND hard_stop_enabled


@dataclass
class BudgetDecision:
    """Aggregate result of evaluating all applicable policies."""
    allowed: bool
    reason: str
    policies_evaluated: list[PolicyResult]
    most_restrictive: PolicyResult | None
    warnings: list[str]


async def evaluate_policies(
    agent_id: str | None = None,
    pipeline_stage: str | None = None,
) -> BudgetDecision:
    """Evaluate ALL applicable budget policies for a given context.

    Checks company, agent, and pipeline_stage scopes.
    Most restrictive policy wins (lowest remaining_usd).
    Returns BudgetDecision with allowed=True/False.

    Feature flag: MULTI_SCOPE_BUDGET_ENABLED (default false).
    When disabled, falls back to legacy $800 check.
    """
    if not os.environ.get("MULTI_SCOPE_BUDGET_ENABLED", "false").lower() in ("true", "1", "yes"):
        # Legacy fallback — single company cap
        spending = await get_month_spending()
        allowed = not spending["exceeded"]
        return BudgetDecision(
            allowed=allowed,
            reason="" if allowed else f"Company cap exceeded: ${spending['total_spent']:.2f}/${spending['remaining']:.2f}",
            policies_evaluated=[],
            most_restrictive=None,
            warnings=[],
        )

    from shared.db import fetch_all, fetch_val

    # Load all policies
    policies = await fetch_all(
        "SELECT * FROM budget_policies ORDER BY scope_type, scope_value"
    )
    if not policies:
        # No policies defined — allow (but warn)
        logger.warning("No budget policies defined — allowing by default")
        return BudgetDecision(
            allowed=True, reason="no_policies_defined",
            policies_evaluated=[], most_restrictive=None, warnings=["No budget policies defined"],
        )

    results: list[PolicyResult] = []
    warnings: list[str] = []

    for policy in policies:
        scope_type = policy["scope_type"]
        scope_value = policy["scope_value"]
        window_kind = policy["window_kind"]
        limit_usd = float(policy["limit_usd"])
        warn_percent = int(policy["warn_percent"])
        hard_stop_enabled = bool(policy["hard_stop_enabled"])

        # Skip inapplicable policies
        if scope_type == "agent" and agent_id != scope_value:
            continue
        if scope_type == "pipeline_stage" and pipeline_stage != scope_value:
            continue

        # Query spend for this policy's scope + window
        spent = await _query_spend_for_policy(scope_type, scope_value, window_kind)
        remaining = limit_usd - spent
        percent_used = (spent / limit_usd * 100) if limit_usd > 0 else 100.0

        warn_triggered = percent_used >= warn_percent
        hard_stop = percent_used >= 100 and hard_stop_enabled

        result = PolicyResult(
            policy_id=policy["policy_id"],
            scope_type=scope_type,
            scope_value=scope_value,
            window_kind=window_kind,
            limit_usd=limit_usd,
            spent_usd=spent,
            remaining_usd=max(0, remaining),
            percent_used=round(percent_used, 1),
            warn_triggered=warn_triggered,
            hard_stop=hard_stop,
        )
        results.append(result)

        if warn_triggered and not hard_stop:
            warnings.append(
                f"Budget warning: {scope_type}/{scope_value} at {percent_used:.1f}% "
                f"(${spent:.2f}/${limit_usd:.2f})"
            )

    # Most restrictive wins (lowest remaining)
    most_restrictive = min(results, key=lambda r: r.remaining_usd) if results else None

    # Any hard stop triggers rejection
    hard_stopped = [r for r in results if r.hard_stop]
    if hard_stopped:
        blocker = hard_stopped[0]
        return BudgetDecision(
            allowed=False,
            reason=f"Hard stop: {blocker.scope_type}/{blocker.scope_value} "
                   f"at {blocker.percent_used:.1f}% (${blocker.spent_usd:.2f}/${blocker.limit_usd:.2f})",
            policies_evaluated=results,
            most_restrictive=most_restrictive,
            warnings=warnings,
        )

    return BudgetDecision(
        allowed=True,
        reason="all_policies_within_limits",
        policies_evaluated=results,
        most_restrictive=most_restrictive,
        warnings=warnings,
    )


async def _query_spend_for_policy(scope_type: str, scope_value: str, window_kind: str) -> float:
    """Query total spend for a policy scope + window from cost_events + budget_tracking.

    Uses cost_events when PER_CALL_COST_EVENTS_ENABLED, otherwise falls back to budget_tracking.
    """
    from shared.db import fetch_val

    # Determine time window
    if window_kind == "monthly":
        time_clause = "AND created_at >= DATE_TRUNC('month', CURRENT_DATE)"
    else:  # lifetime
        time_clause = ""

    # Determine scope filter
    if scope_type == "company":
        scope_clause = ""
        scope_params: tuple = ()
    elif scope_type == "agent":
        scope_clause = "AND agent_id = %s"
        scope_params = (scope_value,)
    elif scope_type == "pipeline_stage":
        scope_clause = "AND task_type = %s"
        scope_params = (scope_value,)
    else:
        return 0.0

    # Try cost_events first (richer data), fall back to budget_tracking
    use_cost_events = os.environ.get(
        "PER_CALL_COST_EVENTS_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    # Build query using separate parameterized paths (NO f-string SQL — AEGIS requirement)
    if use_cost_events:
        if scope_type == "company" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE)"
        elif scope_type == "company":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events"
        elif scope_type == "agent" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND agent_id = %s"
        elif scope_type == "agent":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE agent_id = %s"
        elif scope_type == "pipeline_stage" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND task_type = %s"
        elif scope_type == "pipeline_stage":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events WHERE task_type = %s"
        else:
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM cost_events"
    else:
        # Legacy: budget_tracking + llm_metrics combined (separate query per scope)
        if scope_type == "agent" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND daemon = %s"
        elif scope_type == "agent":
            query = "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE daemon = %s"
        elif scope_type == "pipeline_stage" and window_kind == "monthly":
            query = "SELECT COALESCE(SUM(amount), 0) FROM budget_tracking WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE) AND pipeline_stage = %s"
        elif scope_type == "pipeline_stage":
            query = "SELECT COALESCE(SUM(amount), 0) FROM budget_tracking WHERE pipeline_stage = %s"
        else:
            # Company-wide from llm_metrics
            query = f"SELECT COALESCE(SUM(cost_usd), 0) FROM llm_metrics WHERE TRUE {time_clause}"

    total = await fetch_val(query, scope_params) if scope_params else await fetch_val(query)
    return float(total or 0)
```

**Emit warnings to Hermes:**

When `warn_triggered` is True for any policy, emit an event for Hermes to pick up:

```python
# In evaluate_policies, after computing warnings:
if warnings:
    try:
        from shared.comms import emit_event
        for warning_msg in warnings:
            await emit_event("budget_warning", {
                "message": warning_msg,
                "agent_id": agent_id,
                "pipeline_stage": pipeline_stage,
            })
    except Exception as exc:
        logger.warning("Budget warning event emission failed: %s", exc)
```

**Acceptance criteria:**
- [ ] `evaluate_policies()` loads ALL applicable policies from budget_policies table
- [ ] Company-scope policies always apply; agent/pipeline_stage scoped to context
- [ ] Most restrictive policy wins (lowest remaining)
- [ ] Warn at configured `warn_percent` (default 80%)
- [ ] Hard stop at 100% when `hard_stop_enabled=True`
- [ ] Warnings emitted as events for Hermes alert dispatch
- [ ] Feature flag `MULTI_SCOPE_BUDGET_ENABLED` gates multi-scope (legacy fallback when off)
- [ ] Parameterized SQL only (AEGIS compliant)
- [ ] Default company policy seeded in migration (never empty table)

---

### Task 4: Pre-Execution Budget Gate (BUDGET-01 — Pattern 6)
**Files:**
- `shared/middleware.py` (modify `budget_check_middleware`)
- `shared/cost_events.py` (used for avg cost lookup)
- `tools/budget_guard.py` (used for policy evaluation)

**What:**

Extend `budget_check_middleware` with pre-task cost estimation and AEGIS-compliant fail-closed behavior.

**Modify `shared/middleware.py:budget_check_middleware`:**

```python
async def budget_check_middleware(ctx: dict[str, Any], next_fn: NextFn) -> StageResult:
    """Pre-execution budget gate with cost estimation.

    1. Estimate cost of upcoming task from historical cost_events averages
    2. Evaluate all applicable budget policies (company + agent + stage)
    3. If estimated cost would exceed remaining budget: reject, fall back to Ollama
    4. AEGIS: DB errors -> REJECT and fall back to Ollama (fail CLOSED)

    Feature flag: PRE_EXECUTION_BUDGET_GATE_ENABLED (default false)
    When disabled, falls back to legacy post-hoc budget check.
    """
    pre_gate_enabled = os.environ.get(
        "PRE_EXECUTION_BUDGET_GATE_ENABLED", "false"
    ).lower() in ("true", "1", "yes")

    stage_name = ctx.get("stage_name", "")
    daemon_name = ctx.get("daemon_name", "")

    if pre_gate_enabled:
        try:
            from shared.cost_events import get_average_cost_by_task_type
            from tools.budget_guard import evaluate_policies

            # Step 1: Estimate cost of this task
            estimated_cost = await get_average_cost_by_task_type(stage_name)
            if estimated_cost is None:
                # No historical data — use conservative default ($0.05 per stage)
                estimated_cost = 0.05

            # Step 2: Evaluate all applicable policies
            decision = await evaluate_policies(
                agent_id=daemon_name,
                pipeline_stage=stage_name,
            )

            # Step 3: Check if estimated cost fits within remaining budget
            if not decision.allowed:
                logger.warning(
                    "BUDGET GATE BLOCKED: %s — %s (estimated $%.4f)",
                    stage_name, decision.reason, estimated_cost,
                )
                ctx["budget_fallback_to_ollama"] = True
                return {
                    "success": False,
                    "output": f"Budget gate: {decision.reason}",
                    "budget_blocked": True,
                    "fallback": "ollama",
                }

            if decision.most_restrictive:
                remaining = decision.most_restrictive.remaining_usd
                if estimated_cost > remaining:
                    logger.warning(
                        "BUDGET GATE: estimated $%.4f > remaining $%.2f for %s/%s — rejecting",
                        estimated_cost, remaining,
                        decision.most_restrictive.scope_type,
                        decision.most_restrictive.scope_value,
                    )
                    ctx["budget_fallback_to_ollama"] = True
                    return {
                        "success": False,
                        "output": f"Budget gate: estimated ${estimated_cost:.4f} exceeds remaining ${remaining:.2f}",
                        "budget_blocked": True,
                        "fallback": "ollama",
                    }

            # Attach budget context for downstream use
            ctx["budget_remaining"] = decision.most_restrictive.remaining_usd if decision.most_restrictive else None
            ctx["budget_warnings"] = decision.warnings

        except Exception as exc:
            # AEGIS: Fail CLOSED — DB errors reject and fallback to Ollama
            logger.error(
                "BUDGET GATE DB ERROR — failing CLOSED (rejecting stage '%s'): %s",
                stage_name, exc,
            )
            ctx["budget_fallback_to_ollama"] = True
            return {
                "success": False,
                "output": f"Budget gate: DB error — fail closed ({exc})",
                "budget_blocked": True,
                "fallback": "ollama",
            }

        return await next_fn(ctx)

    # --- Legacy behavior (pre-gate disabled) ---
    # AEGIS FIX: Change from fail-open to fail-closed
    try:
        from shared.observability import get_metrics_summary

        summary = await get_metrics_summary(hours=720)  # ~30 days
        daemons = summary.get("daemons", [])
        total_cost = sum(float(d.get("total_cost_usd", 0) or 0) for d in daemons)

        if total_cost >= _BUDGET_CAP_USD:
            logger.warning(
                "BUDGET EXCEEDED: $%.2f / $%.2f — blocking stage '%s'",
                total_cost, _BUDGET_CAP_USD, stage_name,
            )
            return {
                "success": False,
                "output": f"Budget cap exceeded: ${total_cost:.2f} / ${_BUDGET_CAP_USD:.2f}",
            }
    except Exception as exc:
        # AEGIS FIX: Fail CLOSED — reject and fallback to Ollama
        logger.error(
            "BUDGET CHECK DB ERROR — failing CLOSED (was: failing open): %s", exc,
        )
        return {
            "success": False,
            "output": f"Budget check: DB error — fail closed ({exc})",
            "budget_blocked": True,
            "fallback": "ollama",
        }

    return await next_fn(ctx)
```

**Key changes from current implementation:**
1. **AEGIS fix (both paths):** `except Exception` now returns failure instead of allowing through
2. **Pre-gate path:** Estimates cost from historical averages, evaluates multi-scope policies, rejects if estimated cost > remaining
3. **Fallback signal:** Sets `ctx["budget_fallback_to_ollama"] = True` so downstream LLM calls can auto-downgrade
4. **Feature flag:** `PRE_EXECUTION_BUDGET_GATE_ENABLED` controls new behavior; legacy path always gets the AEGIS fix

**Acceptance criteria:**
- [ ] DB errors in budget check now REJECT (fail closed) — AEGIS compliance
- [ ] Pre-execution cost estimation uses `get_average_cost_by_task_type()` from cost_events
- [ ] Conservative default ($0.05) when no historical data exists
- [ ] Multi-scope policy evaluation via `evaluate_policies()`
- [ ] Estimated cost > remaining triggers rejection with Ollama fallback signal
- [ ] Feature flag `PRE_EXECUTION_BUDGET_GATE_ENABLED` gates new behavior (default false)
- [ ] Legacy path still works but now fails closed on DB errors
- [ ] Budget context (`budget_remaining`, `budget_warnings`) attached to ctx for downstream stages

---

### Task 5: Hermes Cost Breakdown Dashboard Endpoint (BUDGET-03)
**File:** `hermes/web/app.py` (modify — add endpoint)
**What:**

Add GET /api/costs/breakdown endpoint with grouping by time/agent/model.

```python
@app.get("/api/costs/breakdown")
async def api_costs_breakdown(request: Request):
    """Cost breakdown from cost_events table.

    Query params:
      - group_by: 'agent' | 'model' | 'task_type' | 'day' (default: 'agent')
      - days: lookback period in days (default: 30)
    """
    from shared.db import fetch_all

    group_by = request.query_params.get("group_by", "agent")
    days = int(request.query_params.get("days", "30"))

    # Allowlist group_by to prevent SQL injection via dynamic column
    ALLOWED_GROUPS = {
        "agent": "agent_id",
        "model": "model",
        "task_type": "task_type",
        "day": "DATE(created_at)",
    }

    column = ALLOWED_GROUPS.get(group_by)
    if column is None:
        return JSONResponse(
            {"error": f"Invalid group_by: {group_by}. Must be one of: {list(ALLOWED_GROUPS.keys())}"},
            status_code=400,
        )

    try:
        # Safe: column is from allowlist, not user input. days is cast to int above.
        rows = await fetch_all(
            f"""SELECT {column} AS group_key,
                       COUNT(*) AS call_count,
                       COALESCE(SUM(tokens_in), 0) AS total_tokens_in,
                       COALESCE(SUM(tokens_out), 0) AS total_tokens_out,
                       ROUND(COALESCE(SUM(cost_usd), 0)::numeric, 4) AS total_cost_usd,
                       ROUND(COALESCE(AVG(cost_usd), 0)::numeric, 6) AS avg_cost_usd,
                       ROUND(COALESCE(AVG(latency_ms), 0)) AS avg_latency_ms
                FROM cost_events
                WHERE created_at >= NOW() - INTERVAL %s
                GROUP BY {column}
                ORDER BY total_cost_usd DESC""",
            (f"{days} days",),
        )
        return JSONResponse({
            "group_by": group_by,
            "days": days,
            "breakdown": [dict(r) for r in rows] if rows else [],
        })
    except Exception as e:
        return JSONResponse({"error": str(e), "breakdown": []}, status_code=500)
```

**Acceptance criteria:**
- [ ] GET /api/costs/breakdown returns grouped cost data
- [ ] group_by validated against allowlist (no SQL injection)
- [ ] Supports agent, model, task_type, and day grouping
- [ ] Returns call_count, total_tokens_in, total_tokens_out, total_cost_usd, avg_cost_usd, avg_latency_ms
- [ ] Parameterized SQL for the `days` interval
- [ ] Graceful error handling with 500 response

---

### Task 6: Tests
**Files:**
- `tests/shared/test_cost_events.py` (new)
- `tests/tools/test_budget_policies.py` (new)
- `tests/shared/test_budget_middleware_failclosed.py` (new)

**Test: `tests/shared/test_cost_events.py`**

```
- test_cost_event_dataclass_defaults: Verify CostEvent has correct defaults
- test_emit_cost_event_respects_feature_flag: Flag off -> no DB write
- test_emit_cost_event_writes_to_db: Flag on -> DB write with correct params (mock DB)
- test_emit_cost_event_swallows_db_error: DB failure -> logs warning, no raise
- test_get_average_cost_by_task_type_returns_average: Mock DB returns avg
- test_get_average_cost_by_task_type_returns_none_no_data: No rows -> None
- test_get_average_cost_by_task_type_swallows_error: DB error -> None
- test_get_total_spend_current_month: Verify monthly sum query
- test_get_agent_spend: Verify agent-scoped sum query
```

**Test: `tests/tools/test_budget_policies.py`**

```
- test_evaluate_policies_legacy_fallback: Flag off -> uses legacy get_month_spending
- test_evaluate_policies_company_scope: Company policy at 50% -> allowed
- test_evaluate_policies_company_hard_stop: Company at 100% -> blocked
- test_evaluate_policies_agent_scope_filters: Agent policy only applies to matching agent
- test_evaluate_policies_most_restrictive_wins: 3 policies, tightest one is most_restrictive
- test_evaluate_policies_warn_emits_event: 80% usage -> emits budget_warning event
- test_evaluate_policies_no_policies_allows: Empty table -> allowed with warning
- test_evaluate_policies_db_error_propagates: DB failure raises (callers handle)
- test_query_spend_for_policy_monthly: Monthly window uses DATE_TRUNC
- test_query_spend_for_policy_lifetime: Lifetime window has no time filter
- test_query_spend_for_policy_uses_cost_events_when_enabled: Flag on -> cost_events table
- test_query_spend_for_policy_uses_legacy_when_disabled: Flag off -> llm_metrics/budget_tracking
```

**Test: `tests/shared/test_budget_middleware_failclosed.py`**

```
- test_budget_middleware_db_error_rejects_legacy: DB error in legacy path -> fail closed (AEGIS)
- test_budget_middleware_db_error_rejects_pregate: DB error in pre-gate -> fail closed
- test_budget_middleware_pregate_estimates_cost: Estimated cost checked against remaining
- test_budget_middleware_pregate_rejects_over_budget: Estimated > remaining -> blocked
- test_budget_middleware_pregate_allows_within_budget: Estimated < remaining -> allowed
- test_budget_middleware_pregate_default_estimate: No historical data -> $0.05 conservative default
- test_budget_middleware_pregate_attaches_context: budget_remaining in ctx after pass
- test_budget_middleware_legacy_blocks_at_cap: $800 exceeded -> blocked (existing behavior preserved)
- test_budget_middleware_fallback_signal: Blocked -> budget_fallback_to_ollama in ctx
```

**Acceptance criteria:**
- [ ] All tests pass with `PYTHONPATH=. pytest tests/shared/test_cost_events.py tests/tools/test_budget_policies.py tests/shared/test_budget_middleware_failclosed.py -v`
- [ ] `ruff check shared/cost_events.py tools/budget_guard.py shared/middleware.py` clean
- [ ] Tests mock DB calls (no real database required)
- [ ] Tests verify AEGIS fail-closed behavior explicitly

---

## New Files Summary

| File | Purpose |
|------|---------|
| `scripts/migrations/026-budget-cost-patterns.sql` | budget_policies + cost_events tables + default policy seed |
| `shared/cost_events.py` | CostEvent dataclass, emit_cost_event(), avg cost lookup, spend queries |
| `tests/shared/test_cost_events.py` | Cost event emission + query tests |
| `tests/tools/test_budget_policies.py` | Multi-scope policy evaluation tests |
| `tests/shared/test_budget_middleware_failclosed.py` | Budget middleware AEGIS compliance + pre-gate tests |

## Modified Files Summary

| File | Change |
|------|--------|
| `tools/budget_guard.py` | Add PolicyResult, BudgetDecision, evaluate_policies(), _query_spend_for_policy() |
| `shared/middleware.py` | Rewrite budget_check_middleware: pre-gate + AEGIS fail-closed fix |
| `shared/llm_client.py` | Extend _fire_metrics to emit CostEvent alongside existing record_llm_call |
| `hermes/web/app.py` | Add GET /api/costs/breakdown endpoint |

---

## Success Criteria

- [ ] Migration 026 creates budget_policies and cost_events tables
- [ ] Default company policy ($800/month, warn 80%, hard_stop=true) seeded
- [ ] Every LLM call emits a CostEvent when flag enabled
- [ ] Pre-execution budget gate estimates cost from historical averages
- [ ] Multi-scope policies: company, agent, pipeline_stage scopes all evaluated
- [ ] Most restrictive policy wins
- [ ] Warn at 80% emits Hermes event
- [ ] Hard stop at 100% rejects with Ollama fallback
- [ ] Budget check fails CLOSED on DB errors (AEGIS compliance)
- [ ] GET /api/costs/breakdown returns grouped cost data
- [ ] All 3 feature flags default OFF (48hr shadow mode before enforcement)
- [ ] All existing tests continue to pass
- [ ] `ruff check` clean on all modified files

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Fail-closed blocks legitimate work during DB outage | Medium | High | Ollama fallback keeps pipeline running; monitor false-rejection rate in shadow mode |
| cost_events table grows large | Low | Medium | created_at index + periodic archival; partition by month if needed |
| Historical avg cost estimation inaccurate for new task types | Medium | Low | Conservative $0.05 default; 30-day lookback window adjusts quickly |
| Multi-scope policy queries add latency | Low | Low | Simple indexed queries; company policy cached in memory after first load |
| Legacy + new cost recording doubles writes | Low | Low | Temporary during shadow mode; legacy paths removed after 48hr validation |

## Rollout Plan

1. **Deploy migration 026** — creates tables, seeds default policy
2. **Enable `PER_CALL_COST_EVENTS_ENABLED`** — 48hr shadow: cost_events populate alongside existing budget_tracking/llm_metrics
3. **Enable `MULTI_SCOPE_BUDGET_ENABLED`** — 48hr shadow: policy evaluation runs but company $800 policy matches existing behavior
4. **Enable `PRE_EXECUTION_BUDGET_GATE_ENABLED`** — activates pre-task estimation + fail-closed gate
5. **Add agent/stage-specific policies** via budget_policies INSERT as needed

---

*Plan created: 2026-03-30 — Budget & Cost Patterns (Paperclip patterns 6, 7, 8)*
