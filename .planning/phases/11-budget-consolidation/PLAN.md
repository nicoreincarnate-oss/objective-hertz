# Phase 11: Budget Consolidation — PLAN

## Goal

Consolidate four scattered budget enforcement points into a single authority (`shared/middleware.py:budget_check_middleware`) that fails closed on DB errors, enabling Paperclip integration with a predictable spend control plane.

## Prerequisites

- Phase 04 (DeerFlow Async Middleware Chain) complete — `budget_check_middleware` exists and is wired into `MIDDLEWARE_REGISTRY`
- Phase 09 (Audit Gap Closure) complete — AEGIS SQL safety rules enforced
- `ENABLE_MIDDLEWARE=true` active in production (middleware chain executes on Titan pipeline stages)
- All 457 existing tests passing on current `intel-integration` branch
- Docker services running (Postgres for budget_tracking table + v_effective_budget_tracking view)

## Feature Flag

**Name:** `ENABLE_CONSOLIDATED_BUDGET`
**Env var:** `ENABLE_CONSOLIDATED_BUDGET` (checked via `_flag()` helper in `shared/middleware.py`)
**Default:** `false` (old `_budget_gate` behavior preserved until explicit cutover)
**When OFF:** `_budget_gate` in `llm_client.py` runs as-is (fail-open, per-call check); `budget_check_middleware` runs its existing pipeline-stage logic unchanged.
**When ON:** `_budget_gate` becomes a no-op passthrough (returns `requested_model` unchanged); `budget_check_middleware` gains per-call model downgrade logic (Ollama fallback) and fails closed on DB error.

## Tasks

### Plan 11-01: Upgrade budget_check_middleware to consolidated authority

#### Task 1: Add consolidated budget query to budget_check_middleware
**File:** `shared/middleware.py`
**Action:** modify
**Details:**
- Add import: `from datetime import date` (line ~19, alongside existing datetime import)
- Add import: `from shared.config import config` (lazy import inside function body to match existing pattern)
- Below `_BUDGET_CAP_USD = 800.0` (line 385), add constants:
  ```python
  _ALERT_THRESHOLD = 0.8  # Downgrade Haiku at 80%
  ```
- Inside `budget_check_middleware` (line 388), add a new code path gated by `_flag("ENABLE_CONSOLIDATED_BUDGET")`:
  - When flag is ON:
    1. Query `v_effective_budget_tracking` using parameterized SQL via `shared.db.fetch_val`:
       ```python
       from shared.db import fetch_val
       month = date.today().replace(day=1)
       total = await fetch_val(
           "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
           (month,),
       ) or 0
       ```
    2. Compute `percent_used = float(total) / _BUDGET_CAP_USD if _BUDGET_CAP_USD > 0 else 1.0`
    3. Read `ctx.get("requested_model")` — if present, this is a per-LLM-call check (injected by `LLMClient.generate`):
       - If `percent_used >= 1.0`: set `ctx["resolved_model"] = "local"`, log warning
       - If `percent_used >= _ALERT_THRESHOLD` and `requested_model == "fast"`: set `ctx["resolved_model"] = "local"`, log info
       - Otherwise: set `ctx["resolved_model"] = requested_model` (no downgrade)
       - Return `await next_fn(ctx)` (stage continues, model decision is in ctx)
    4. If `requested_model` NOT in ctx, this is a pipeline-stage check — use existing logic (block stage if `total >= _BUDGET_CAP_USD`)
  - When flag is OFF: existing behavior unchanged (query `get_metrics_summary`, block stage on exceed)
  - **Critical: fail-closed exception handler** when flag is ON:
    ```python
    except Exception as exc:
        logger.error("Budget check DB failed — REJECTING call (fail-closed): %s", exc)
        if "requested_model" in ctx:
            ctx["resolved_model"] = "local"  # Force Ollama
            return await next_fn(ctx)
        return {"success": False, "output": f"Budget check unavailable: {exc}"}
    ```
  - The existing fail-open `except` block (line 411-413) stays for the flag-OFF path only

#### Task 2: Add per-call budget check entry point for LLMClient
**File:** `shared/middleware.py`
**Action:** modify
**Details:**
- Add a new public async function after `budget_check_middleware`:
  ```python
  async def check_budget_for_llm_call(requested_model: str) -> str:
      """Standalone budget check for LLM calls (used by LLMClient when consolidated flag is ON).

      Returns the resolved model — either the requested model or "local" for Ollama fallback.
      Fails CLOSED: DB errors return "local".
      """
      if not _flag("ENABLE_CONSOLIDATED_BUDGET"):
          return requested_model  # No-op when flag is off

      try:
          from shared.db import fetch_val
          month = date.today().replace(day=1)
          total = await fetch_val(
              "SELECT COALESCE(SUM(amount), 0) FROM v_effective_budget_tracking WHERE month = %s",
              (month,),
          ) or 0

          cap = _BUDGET_CAP_USD
          percent_used = float(total) / cap if cap > 0 else 1.0

          if percent_used >= 1.0:
              logger.warning("Budget exceeded ($%.2f/$%.2f) — forcing Ollama", float(total), cap)
              return "local"

          if percent_used >= _ALERT_THRESHOLD and requested_model == "fast":
              logger.info("Budget at %.0f%% — downgrading fast to Ollama", percent_used * 100)
              return "local"

          return requested_model

      except Exception as exc:
          logger.error("Budget DB failed — fail-closed, forcing Ollama: %s", exc)
          return "local"
  ```
- Add `"check_budget_for_llm_call"` to the `__all__` list (line ~438)

#### Task 3: Wire LLMClient.generate to use consolidated budget check
**File:** `shared/llm_client.py`
**Action:** modify
**Details:**
- At line 192-193, replace:
  ```python
  # Budget check — downgrade Claude to Ollama when needed
  resolved_model = await self._budget_gate(model)
  ```
  with:
  ```python
  # Budget check — consolidated middleware or legacy gate
  if os.environ.get("ENABLE_CONSOLIDATED_BUDGET", "").lower() in ("true", "1", "yes"):
      from shared.middleware import check_budget_for_llm_call
      resolved_model = await check_budget_for_llm_call(model)
  else:
      resolved_model = await self._budget_gate(model)
  ```
- Add `import os` to the imports section (line ~18) if not already present
- At line 243 (inside `generate_with_images`), apply the same conditional:
  ```python
  if os.environ.get("ENABLE_CONSOLIDATED_BUDGET", "").lower() in ("true", "1", "yes"):
      from shared.middleware import check_budget_for_llm_call
      model = await check_budget_for_llm_call(model)
  else:
      model = await self._budget_gate(model)
  ```
- Do NOT remove `_budget_gate` method yet — it stays as the flag-OFF fallback (removed in Commit 2)

#### Task 4: Read budget cap from config instead of hardcoded constant
**File:** `shared/middleware.py`
**Action:** modify
**Details:**
- Change `_BUDGET_CAP_USD = 800.0` (line 385) to:
  ```python
  def _get_budget_cap() -> float:
      """Read budget cap from config, defaulting to $800."""
      try:
          from shared.config import config
          return float(config.budget.monthly_cap)
      except Exception:
          return 800.0
  ```
- Replace all references to `_BUDGET_CAP_USD` inside `budget_check_middleware` and `check_budget_for_llm_call` with `_get_budget_cap()`
- This aligns with how `_budget_gate` and `BudgetGuard` already read from `config.budget.monthly_cap`

### Plan 11-02: Update test suite for consolidated behavior

#### Task 5: Add fail-closed tests for consolidated budget middleware
**File:** `tests/shared/test_middleware.py`
**Action:** modify
**Details:**
- Add new test class `TestConsolidatedBudgetMiddleware` after existing `TestBudgetCheckMiddleware` (line ~459):
  - `test_consolidated_under_budget_allows_call` — flag ON, $100 spend, `requested_model="fast"` in ctx, asserts `ctx["resolved_model"] == "fast"`
  - `test_consolidated_at_threshold_downgrades_fast` — flag ON, $650 spend, `requested_model="fast"`, asserts `ctx["resolved_model"] == "local"`
  - `test_consolidated_at_threshold_keeps_smart` — flag ON, $650 spend, `requested_model="smart"`, asserts `ctx["resolved_model"] == "smart"`
  - `test_consolidated_exceeded_forces_all_local` — flag ON, $850 spend, both fast and smart resolve to `"local"`
  - `test_consolidated_db_failure_fails_closed` — flag ON, `fetch_val` raises Exception, asserts `ctx["resolved_model"] == "local"` (NOT the requested model)
  - `test_consolidated_pipeline_stage_blocks_on_exceed` — flag ON, no `requested_model` in ctx, $850 spend, asserts `result["success"] is False`
  - `test_consolidated_pipeline_stage_db_failure_blocks` — flag ON, no `requested_model` in ctx, DB error, asserts `result["success"] is False`
  - All tests mock `shared.db.fetch_val` and use `patch.dict(os.environ, {"ENABLE_CONSOLIDATED_BUDGET": "true"})`

#### Task 6: Add tests for check_budget_for_llm_call standalone function
**File:** `tests/shared/test_middleware.py`
**Action:** modify
**Details:**
- Add test class `TestCheckBudgetForLlmCall`:
  - `test_flag_off_returns_requested_model` — flag OFF, returns input model unchanged without DB call
  - `test_under_budget_passes_through` — flag ON, $100 spend, returns `"fast"`
  - `test_exceeded_returns_local` — flag ON, $850 spend, returns `"local"`
  - `test_db_error_returns_local` — flag ON, DB raises, returns `"local"` (fail-closed)
  - `test_threshold_downgrades_fast_only` — flag ON, $650 spend, `"fast"` -> `"local"`, `"smart"` -> `"smart"`

#### Task 7: Update existing budget gate tests for feature flag awareness
**File:** `tests/test_budget_gate.py`
**Action:** modify
**Details:**
- Add `test_db_failure_fails_closed_when_consolidated` — with `ENABLE_CONSOLIDATED_BUDGET=true`, mock `check_budget_for_llm_call` to return `"local"`, verify LLMClient.generate routes to Ollama
- Update docstring of `test_db_failure_fails_open` to clarify it only applies when `ENABLE_CONSOLIDATED_BUDGET=false`
- Add `test_flag_off_preserves_legacy_behavior` — with flag OFF, verify `_budget_gate` is still called (not `check_budget_for_llm_call`)

#### Task 8: Update budget source regression test
**File:** `tests/test_budget_sources.py`
**Action:** modify
**Details:**
- Add `test_middleware_budget_uses_effective_budget_view`:
  ```python
  def test_middleware_budget_uses_effective_budget_view():
      code = (ROOT / "shared" / "middleware.py").read_text()
      assert "v_effective_budget_tracking" in code
  ```
- Add `test_middleware_budget_fails_closed`:
  ```python
  def test_middleware_budget_fails_closed():
      """Consolidated budget path must NOT contain fail-open patterns."""
      code = (ROOT / "shared" / "middleware.py").read_text()
      # The check_budget_for_llm_call function must return "local" on error
      assert 'return "local"' in code
  ```

### Plan 11-03: Commit 2 — Remove legacy _budget_gate (after verification)

This is a SEPARATE commit, deployed only after Commit 1 is verified in production with `ENABLE_CONSOLIDATED_BUDGET=true`.

#### Task 9: Remove _budget_gate from LLMClient
**File:** `shared/llm_client.py`
**Action:** modify
**Details:**
- Delete `_budget_gate` method entirely (lines 271-298)
- Replace the conditional at line 192 with direct call:
  ```python
  from shared.middleware import check_budget_for_llm_call
  resolved_model = await check_budget_for_llm_call(model)
  ```
- Replace the conditional at line 243 (generate_with_images) with direct call:
  ```python
  from shared.middleware import check_budget_for_llm_call
  model = await check_budget_for_llm_call(model)
  ```
- Remove the `ENABLE_CONSOLIDATED_BUDGET` conditional checks from both call sites
- Update module docstring (lines 1-16) to remove references to `_budget_gate` and note that budget enforcement lives in `shared/middleware.py`

#### Task 10: Update tests to remove legacy _budget_gate references
**File:** `tests/test_budget_gate.py`
**Action:** modify
**Details:**
- Rename file conceptually (but do NOT rename to preserve imports) — update module docstring to indicate these now test the consolidated path
- Remove `TestBudgetGate` class entirely (tests `_budget_gate` which no longer exists)
- Replace with `TestConsolidatedBudgetRouting` that tests `LLMClient.generate` end-to-end:
  - Mock `check_budget_for_llm_call` to return various values
  - Verify `generate()` routes to Ollama when `check_budget_for_llm_call` returns `"local"`
  - Verify `generate()` uses Claude when `check_budget_for_llm_call` returns `"smart"`

#### Task 11: Update test_llm_local_routing.py
**File:** `tests/test_llm_local_routing.py`
**Action:** modify
**Details:**
- Line 31: replace `client._budget_gate = AsyncMock(side_effect=lambda model: model)` with mock of `check_budget_for_llm_call`
- Line 77: replace `client._budget_gate = AsyncMock(return_value="local")` with mock of `check_budget_for_llm_call`
- Use `patch("shared.middleware.check_budget_for_llm_call", ...)` instead of setting private method

#### Task 12: Remove feature flag conditional (hardcoded ON)
**File:** `shared/middleware.py`
**Action:** modify
**Details:**
- In `check_budget_for_llm_call`: remove the `if not _flag("ENABLE_CONSOLIDATED_BUDGET")` early return — function always executes
- Optionally keep the flag check with a deprecation warning for one release cycle

#### Task 13: Update budget_guard.py docstring
**File:** `tools/budget_guard.py`
**Action:** modify
**Details:**
- Update module docstring (line 1) to:
  ```python
  """Budget Guard — Reporting tool for monthly budget status.

  NOTE: Budget ENFORCEMENT lives in shared/middleware.py:check_budget_for_llm_call.
  This module is for reporting and can_spend checks only — it does not gate LLM calls.
  """
  ```

## Database Migration

**None.** This phase uses the existing `v_effective_budget_tracking` view and `budget_tracking` table. No schema changes required. The `_record_claude_spend` function in `llm_client.py` continues to write cost records as-is.

## Tests Required

1. **Fail-closed DB error tests** (Task 5) — Verify `check_budget_for_llm_call` returns `"local"` when DB is down
2. **Fail-closed middleware tests** (Task 5) — Verify `budget_check_middleware` blocks pipeline stages when DB is down (flag ON)
3. **Threshold downgrade tests** (Task 6) — Verify `"fast"` downgrades at 80%, `"smart"` only at 100%
4. **Feature flag isolation tests** (Tasks 6-7) — Verify flag OFF preserves exact legacy behavior
5. **Budget source regression** (Task 8) — Verify `v_effective_budget_tracking` view is used (not raw table)
6. **End-to-end LLM routing** (Task 10) — Verify `LLMClient.generate()` routes correctly through consolidated path
7. **Existing 457 tests pass** — No regressions

## Success Criteria

- [ ] `ENABLE_CONSOLIDATED_BUDGET=false`: all 457 existing tests pass, `_budget_gate` runs as before
- [ ] `ENABLE_CONSOLIDATED_BUDGET=true`: new tests pass, `check_budget_for_llm_call` is the sole budget authority
- [ ] DB error during budget check returns `"local"` (fail-closed), not `requested_model` (fail-open)
- [ ] `tools/budget_guard.py` has zero enforcement behavior (reporting only)
- [ ] `_record_claude_spend` in `llm_client.py` is untouched (cost recording is orthogonal to enforcement)
- [ ] Commit 1 is independently deployable (flag OFF = no behavior change)
- [ ] Commit 2 is independently deployable (only after flag ON is verified)
- [ ] `ruff check` passes with zero errors
- [ ] `grep -rn "f\".*SELECT\|f\".*INSERT" shared/middleware.py` returns zero hits (AEGIS SQL safety)
- [ ] Budget cap reads from `config.budget.monthly_cap` (not hardcoded $800)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Middleware not wired in all code paths (e.g., `generate_with_images` missed) | Medium | High | Task 3 explicitly handles both `generate()` and `generate_with_images()` call sites; grep for `_budget_gate` confirms only two call sites |
| `check_budget_for_llm_call` import fails at runtime | Low | High | Lazy import inside function body matches existing codebase pattern; test coverage confirms import works |
| Feature flag OFF but old `_budget_gate` already removed | Medium | Critical | Two-commit strategy enforced — Task 9 is a separate commit, only deployed after Task 1-8 are verified with flag ON |
| `get_metrics_summary` (observability) vs `v_effective_budget_tracking` (DB view) data mismatch | Medium | Medium | Consolidated path uses `v_effective_budget_tracking` directly (same source as `_budget_gate` and `BudgetGuard`), eliminating the observability layer indirection |
| Conway crypto spending not included in middleware check | Low | Low | `BudgetGuard.get_month_spending` includes Conway; consolidated path queries same view. If Conway spending needs inclusion, it is a follow-up enhancement, not a blocker |
| Circular import: `llm_client.py` imports from `middleware.py` | Low | Medium | Lazy import inside function body (`from shared.middleware import check_budget_for_llm_call`) avoids module-level circular dependency; matches existing `from shared.db import fetch_val` pattern |
