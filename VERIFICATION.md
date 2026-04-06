# Phase 19: Cost Measurement + Telemetry -- VERIFICATION

## Implementation Summary

Phase 19 builds the cost visibility and telemetry instrumentation layer for Objective Hertz. All changes are gated behind the `ANATOMY_COST_DASHBOARD` feature flag (default: off).

### What Was Built

#### Plan 19-01: Agent Tick Cost Tracking Dashboard (D-24)

| Component | File | Status |
|-----------|------|--------|
| Weekly agent cost report (cost_events table) | `shared/cost_events.py` | NEW: `get_weekly_agent_cost_report()`, `get_agent_cost_summary()` |
| Cost dashboard (budget_tracking table) | `shared/cost_dashboard.py` | PRE-EXISTING: Full implementation with weekly report, summary, text formatting |
| Cost dashboard API endpoint | `hermes/web/app.py` | NEW: `GET /api/cost-dashboard?days=7` |
| Retrieval telemetry API endpoint | `hermes/web/app.py` | NEW: `GET /api/retrieval-telemetry?days=7&query_type=...` |
| Cost summary in morning briefing | `hermes/a2a_server.py` | NEW: `_briefing_generate()` includes `cost_dashboard` key |
| AgentManager Postgres sync | `openjarvis/agents/manager.py` | NEW: `sync_cost_from_postgres()` method |

#### Plan 19-02: Retrieval Telemetry -- magma_retrieval_stats (E-04)

| Component | File | Status |
|-----------|------|--------|
| DB migration 030 | `scripts/migrations/030-magma-retrieval-stats.sql` | ENHANCED: Added query_type, confidence_max, result_tokens, cache_hit, client_id, daemon, meta_params columns + 7 indexes |
| Telemetry recording function | `shared/magma.py` | PRE-EXISTING: `_record_retrieval_stats()` |
| magma_retrieve() instrumentation | `shared/magma.py` | PRE-EXISTING: timing wrappers on all 4 phases |
| ALMA feedback from telemetry | `shared/magma.py` | NEW: `compute_alma_adjustments_from_telemetry()` |

#### Plan 19-03: Eliminate Redundant LLM Calls (F-24)

| Component | File | Status |
|-----------|------|--------|
| _ask() response cache (Hermes) | `hermes/a2a_server.py` | PRE-EXISTING: `_ResponseCache` with 5-min TTL |
| _ask() response cache (ClawdBot) | `clawdbot/a2a_server.py` | PRE-EXISTING: `_ResponseCache` with 5-min TTL |
| Batch _review_findings (Hermes) | `hermes/a2a_server.py` | PRE-EXISTING: `review_findings_batch()` |
| Batch _review_findings (ClawdBot) | `clawdbot/a2a_server.py` | PRE-EXISTING: `review_findings_batch()` |

#### Plan 19-04: Single-Prompt Pipeline Evaluation (F-23)

| Component | File | Status |
|-----------|------|--------|
| Single-prompt experiment module | `titan/single_prompt_experiment.py` | NEW: Shadow-mode evaluation of collapsed pipeline |

#### Plan 19-05: Speculative Parallel Execution (F-19)

| Component | File | Status |
|-----------|------|--------|
| Speculative research queue | `titan/pipeline/speculative_research.py` | PRE-EXISTING: Producer-consumer with score threshold filtering |

### Feature Flag

- **Name:** `ANATOMY_COST_DASHBOARD`
- **Default:** `false` (zero behavioral change when off)
- **Rollback:** Set to `false` to disable all Phase 19 features instantly

### Test Results

```
44 passed in 0.39s

tests/test_phase19_cost_dashboard.py       -- 18 tests (weekly report, summary, tier extraction, formatting)
tests/test_phase19_redundant_calls.py      -- 15 tests (response cache, batch review, fork dispatch)
tests/test_phase19_retrieval_telemetry.py  -- 5 tests (insert, timing, flag off, DB error, truncation)
tests/test_phase19_speculative.py          -- 6 tests (queue, score filtering, cache, operative caching)
```

### Commits

| Hash | Type | Description |
|------|------|-------------|
| `409a3aa` | feat | Weekly agent cost report and summary in cost_events |
| `643472d` | feat | Cost-dashboard and retrieval-telemetry API endpoints |
| `b3b5113` | feat | Wire cost summary into morning briefing |
| `6df221a` | feat | Add sync_cost_from_postgres to AgentManager |
| `bdb96c0` | feat | ALMA telemetry adjustments from retrieval stats |
| `d34471e` | feat | Enhance migration 030 with full telemetry columns |
| `2f923c8` | feat | Create single-prompt pipeline experiment module |
| `2104355` | fix | Fix test assertions for narrowed exception types and import stubs |

### Deviations from Plan

1. **cost_events.py vs cost_dashboard.py split**: The plan called for adding `get_weekly_agent_cost_report()` and `get_agent_cost_summary()` to `cost_events.py`. Both were added there as specified. However, `shared/cost_dashboard.py` already existed with equivalent functions querying `budget_tracking` table instead. Both implementations are now available -- `cost_events.py` queries the `cost_events` table (per-call data), while `cost_dashboard.py` queries `budget_tracking` (aggregated billing data). The API endpoint uses the `cost_events` version as the plan specified.

2. **Pre-existing implementations**: Plans 19-03 (response caching, batch review) and 19-05 (speculative research) were already fully implemented in the codebase from a prior integration pass. No changes were needed for these.

3. **Test fixes (Rule 1 - Bug)**: Fixed 5 failing tests:
   - Changed `Exception("db down")` to `ConnectionError("db down")` in cost dashboard tests to match narrowed IGUS exception types
   - Changed `Exception("connection refused")` to `OSError("connection refused")` in retrieval telemetry tests
   - Added `psycopg.Error` class to fake psycopg module
   - Fixed operative import stubs to handle module conflicts across test files

### Known Stubs

None. All implementations are fully wired to their data sources.
