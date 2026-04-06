# Phase 31: Budget + Context + Routing Intelligence — VERIFICATION

**Status:** COMPLETE
**Date:** 2026-04-05
**Tests:** 61 passed, 0 failed

---

## Task 1: BATS — Budget-Aware Tool-Use Scaling

### 1a. Unified Cost Metric
- [x] `UnifiedBudget` dataclass in `shared/cost_events.py`
- [x] Token + tool cost tracking with regime awareness
- [x] Four regimes: HIGH (>=70%), MEDIUM (30-70%), LOW (10-30%), CRITICAL (<10%)
- [x] `record_token_spend()` and `record_tool_use()` methods
- [x] `format_status()` produces compact budget message (~80 tokens)
- [x] `regime_hint` returns behavioral guidance per regime
- [x] `DEFAULT_TOOL_PRICES` for internal/external/no-cost tools
- [x] `create_default_budget()` factory function

### 1b. Four-Layer Budget Constraints
- [x] `BATS_HARD_LIMITS` config in `shared/middleware.py`
- [x] Layer 1: Per-call $2.00 max — reject
- [x] Layer 2: 15-min circuit breaker $2.00 — downgrade to Haiku
- [x] Layer 3: Hourly $4.00 — downgrade to Haiku
- [x] Layer 4: Daily $35.00 — downgrade to Ollama
- [x] `check_bats_constraints()` function for direct checks
- [x] `bats_budget_middleware()` async middleware
- [x] `record_bats_spend()` in-memory tracking
- [x] Falls back to legacy budget_check when BATS disabled

### 1c. Budget Tracker Injection
- [x] `_budget` attribute on `OperativeAgent.__init__`
- [x] Budget status messages injected after each tool result (sync loop)
- [x] Budget status messages injected after each tool result (generator loop)
- [x] `_is_bats_enabled()` static method on OperativeAgent
- [x] Tool use recorded via `_budget.record_tool_use()`

### 1d. Cost Event Enhancement
- [x] `CostEvent.tool_name` field added
- [x] `CostEvent.budget_regime` field added
- [x] `log_budget_decision()` async function for audit trail

## Task 2: BACM-lite — Hierarchical Importance Compression

### 2a. Segment Importance Scoring
- [x] `score_segment_importance()` in `openjarvis/sessions/compression.py`
- [x] Recency weighting (35%)
- [x] Tool result boost (25%)
- [x] Task relevance scoring (25%)
- [x] System message boost (15%)
- [x] Score capped at 1.0

### 2b. Budget-Conditional Compression
- [x] `BACMCompressor` class
- [x] NULL action at >50% budget_ratio
- [x] PARTIAL compression (bottom 40%) at 25-50%
- [x] PARTIAL aggressive (bottom 70%) at 10-25%
- [x] FULL compression (all but current) at <10%
- [x] `compress()` for dict messages
- [x] `compress_messages()` for Message objects (LoopGuard compat)
- [x] `_segment_messages()` by tool-call/response pairs

### 2c. Wire into LoopGuard
- [x] `set_budget()` method on LoopGuard
- [x] `_is_bacm_enabled()` static method
- [x] `_bacm_compress()` called before structural compression
- [x] BACM uses budget regime to determine compression intensity
- [x] `current_task` config for relevance scoring

## Task 3: UGO — Utility-Guided Orchestration

### 3a. Utility Scorer
- [x] `UtilityScore` dataclass in `shared/capability_router.py`
- [x] `score_actions()` evaluates respond/retrieve/tool_call/verify/stop
- [x] `_estimate_gain()` heuristic per action type
- [x] `_estimate_cost()` normalized via budget
- [x] `_estimate_redundancy()` from action history + hashes
- [x] Budget-regime bias: LOW/CRITICAL biases toward respond/stop
- [x] Custom lambda weights support

### 3b. Wire into Operative Agent Loop
- [x] Budget tracker injected into operative context
- [x] UGO action selection available via `score_actions()`

### 3c. Lambda Tuning via Bandit
- [x] `UGO_LAMBDA_ARMS` in `shared/bandit.py`
- [x] Arm A (default): 0.3, 0.5, 0.8
- [x] Arm B (cost-aggressive): 0.6, 0.3, 0.8
- [x] Arm C (exploration-friendly): 0.2, 0.7, 0.5
- [x] `select_ugo_lambdas()` async function
- [x] `update_ugo_lambdas()` with reward signal

## Task 4: Integration Wiring

### 4a. BATS <-> BACM Integration
- [x] BACM reads `UnifiedBudget.remaining_pct` for compression intensity
- [x] LoopGuard bridges BATS budget to BACM via `set_budget()`
- [x] Feedback loop bounded: BACM can only compress, BATS can only constrain

### 4b. BATS <-> UGO Integration
- [x] UGO reads `budget.budget_regime` for regime bias
- [x] UGO normalizes tool cost against `budget.token_budget_usd`
- [x] LOW/CRITICAL regime biases toward respond/stop

### 4d. Governance Integration
- [x] `bats_budget` registered in MIDDLEWARE_REGISTRY
- [x] Budget decisions logged via `log_budget_decision()`

## Database Migration
- [x] `scripts/migrations/043-budget-context-routing.sql`
- [x] `budget_decisions` table with indices
- [x] `compression_stats` table with indices
- [x] `utility_scores` table with indices
- [x] Feature flags inserted into system_config

## Feature Flags
- [x] `BATS_ADAPTIVE_BUDGET` (default true)
- [x] `BACM_COMPRESSION` (default true)
- [x] `UGO_UTILITY_ROUTING` (default true)

## Tests
- `tests/test_phase31_bats.py` — 12 tests (UnifiedBudget, CostEvent, create_default_budget)
- `tests/test_phase31_middleware.py` — 10 tests (constraints, middleware, downgrades)
- `tests/test_phase31_bacm.py` — 13 tests (scoring, compression levels, segmentation)
- `tests/test_phase31_ugo.py` — 14 tests (scoring, gains, costs, redundancy, lambdas)
- `tests/test_phase31_bandit.py` — 6 tests (arms, selection, updates)
- `tests/test_phase31_integration.py` — 6 tests (BATS-BACM, BATS-UGO, LoopGuard wiring)
- **Total: 61 tests, all passing**

## Code Quality
- `ruff check` clean on all 13 files
- Python 3.9 compatible (no `X | Y` in runtime, no `datetime.UTC`, no `zip(strict=)`)
- All imports use `from __future__ import annotations`

## Files Modified
- `shared/cost_events.py` — UnifiedBudget, CostEvent enhancements, log_budget_decision
- `shared/middleware.py` — BATS four-layer constraints, bats_budget_middleware
- `shared/capability_router.py` — UGO UtilityScore, score_actions
- `shared/bandit.py` — UGO lambda arms, select/update functions
- `openjarvis/agents/operative.py` — budget tracker injection, tool use recording
- `openjarvis/sessions/compression.py` — BACMCompressor, score_segment_importance
- `openjarvis/agents/loop_guard.py` — BACM wiring, set_budget, current_task config

## Files Created
- `scripts/migrations/043-budget-context-routing.sql`
- `tests/test_phase31_bats.py`
- `tests/test_phase31_middleware.py`
- `tests/test_phase31_bacm.py`
- `tests/test_phase31_ugo.py`
- `tests/test_phase31_bandit.py`
- `tests/test_phase31_integration.py`
