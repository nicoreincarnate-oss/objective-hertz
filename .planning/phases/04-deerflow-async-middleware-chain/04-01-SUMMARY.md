---
phase: 4
plan: 04-01
subsystem: shared/middleware
tags: [middleware, async, pipeline, deerflow, telemetry, budget, dna, anti-slop, memory]
dependency_graph:
  requires: [phase-0b-async-engine, phase-1-agent-dna, phase-2-anti-slop, phase-3-deerflow-memory]
  provides: [middleware-chain, stage-metrics-table, pipeline-middleware-config]
  affects: [titan-pipeline, clawdbot-pipeline, hermes-pipeline, perseus-pipeline]
tech_stack:
  added: [MiddlewareChain, async-compose-pattern]
  patterns: [recursive-index-compose, fire-and-forget-telemetry, feature-flag-gating, fail-open-middleware]
key_files:
  created:
    - shared/middleware.py
    - scripts/migrations/020-stage-metrics.sql
  modified: []
decisions:
  - "All 5 middlewares in single module (shared/middleware.py) for cohesion"
  - "Budget check uses 720h window (~30 days) for monthly cap"
  - "Telemetry and memory failures are non-fatal (fire-and-forget)"
  - "DNA guard fails open on load errors (allows stage execution)"
  - "Anti-slop secret detection is a hard block (never sends secrets)"
  - "MIDDLEWARE_REGISTRY maps string names to functions for config-driven chains"
metrics:
  duration: 3min
  completed: "2026-03-29T22:24:00Z"
  tasks: 7
  files: 2
---

# Phase 4 Plan 01: Middleware Chain + All Middlewares + Migration Summary

Async middleware chain with recursive compose pattern, 5 middleware implementations (budget, DNA guard, anti-slop, memory, telemetry), per-pipeline config with env override, and stage_metrics migration 020.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | MiddlewareChain core | d8bdd90 | shared/middleware.py |
| 2 | Per-pipeline config + env override | 6d6ccdd | shared/middleware.py |
| 3 | MemoryMiddleware | 6f8d446 | shared/middleware.py |
| 4 | DNAGuardMiddleware | 86ae072 | shared/middleware.py |
| 5 | AntiSlopMiddleware | 44354d8 | shared/middleware.py |
| 6 | TelemetryMiddleware + migration 020 | a043b44 | shared/middleware.py, scripts/migrations/020-stage-metrics.sql |
| 7 | BudgetCheckMiddleware | cbddbcd | shared/middleware.py |

## Key Implementation Details

### MiddlewareChain (MW-01)
- Recursive index-based compose: `_compose(index, ctx)` calls middleware or handler
- First added = outermost wrapper (budget check wraps everything)
- `use()` returns self for fluent chaining

### Per-Pipeline Config (MW-02, MW-08)
- `PIPELINE_CONFIGS` dict: titan (5 MW), clawdbot (4), hermes (3), perseus (2)
- `build_chain(pipeline_name)` constructs from config
- Env override: `TITAN_MIDDLEWARE_ORDER=budget_check,dna_guard,telemetry`
- Validation: warns if anti_slop before dna_guard

### MemoryMiddleware (MW-03)
- Pre-stage: loads episodic memories into `ctx["memories"]`
- Post-stage: saves outcome as episodic memory
- Gated by `ENABLE_DEERFLOW_MEMORY`

### DNAGuardMiddleware (MW-04)
- Checks tool permissions against DNA profile boundaries
- Blocks unauthorized tools with descriptive error
- Gated by `ENABLE_DNA_PROFILES`

### AntiSlopMiddleware (MW-05)
- Only applies to content stages: email_compose, site_build, alert_compose
- Secret detection is a hard block (returns failure)
- Quality scoring adds `result["quality_scores"]`
- Gated by `ENABLE_ANTI_SLOP`

### TelemetryMiddleware (MW-06, MW-07)
- Records stage timing and success to `stage_metrics` table
- Fire-and-forget DB insert (never blocks pipeline)
- Migration 020: stage_metrics with pipeline/stage/daemon indexes

### BudgetCheckMiddleware
- Checks 30-day LLM cost via `get_metrics_summary(hours=720)`
- Hard blocks if total cost >= $800
- Fails open on budget check errors

## Decisions Made

1. All middlewares in single module for cohesion and simple imports
2. Budget check uses 720h window for monthly cap approximation
3. All middleware failures except secret detection are non-fatal
4. MIDDLEWARE_REGISTRY dict enables string-based config-driven chain building
5. Anti-slop secret detection is a hard block (security requirement from Phase 2)
6. DNA guard fails open on load errors to avoid blocking the pipeline

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all middleware implementations are fully wired to their respective Phase 1-3 modules.

## Verification

- `ruff check shared/middleware.py` -- clean
- AST parse -- valid Python
- Chain execution order test -- outermost-first verified
- All imports resolve correctly
- MIDDLEWARE_REGISTRY contains all 5 entries
- PIPELINE_CONFIGS contains all 4 pipelines

## Self-Check: PASSED

All 2 files found on disk. All 7 commit hashes verified in git log.
