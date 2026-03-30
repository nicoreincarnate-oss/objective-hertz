---
phase: 5
plan: 05-01
subsystem: titan/pipeline
tags: [rlm, email-composition, recursive-retrieval, budget-control, mem0, qdrant]
dependency_graph:
  requires: [shared/anti_slop.py, shared/observability.py, shared/llm_client.py, shared/db.py]
  provides: [titan/pipeline/rlm_composer.py, scripts/migrations/021-rlm-context.sql]
  affects: [titan/pipeline/email_compose.py]
tech_stack:
  added: [mem0 (optional), qdrant_client (optional)]
  patterns: [recursive-refinement-loop, graceful-degradation, budget-caps]
key_files:
  created:
    - titan/pipeline/rlm_composer.py
    - scripts/migrations/021-rlm-context.sql
  modified: []
decisions:
  - "All RLM code in single module (rlm_composer.py) for cohesion — same pattern as anti_slop.py"
  - "Mem0/Qdrant are optional imports — graceful degradation with warnings, never crashes"
  - "Budget check uses 720h window for monthly cap (same as middleware budget in Phase 4)"
  - "Returns highest-scoring version across iterations, not latest"
  - "Context expansion targets weakest non-slop dimension specifically"
metrics:
  duration: 2min
  completed: "2026-03-30T00:16:00Z"
---

# Phase 5 Plan 01: RLM Composer + Context + Budget Summary

RLMComposer with 3-iteration draft-evaluate-refine loop using AntiSlopScorer, Mem0 research storage, Qdrant vector retrieval, and per-email/monthly spend caps.

## What Was Built

### Task 1-4: RLMComposer (titan/pipeline/rlm_composer.py)

Single cohesive module containing:

- **RLMComposer.compose()** — draft->evaluate->refine loop, max 3 iterations, returns highest-scoring version
- **store_research_context()** — Mem0 write path for per-lead research storage with mem0_context_id linking
- **_retrieve_context()** / **_expand_context()** — Qdrant read path for top-10 vector retrieval, dimension-targeted expansion
- **_check_budget()** — per-email ($0.08) and monthly ($100) caps via llm_metrics table query
- **_generate_draft()** — builds prompts with lead research, context, and iteration feedback
- Feature flag: `ENABLE_RLM` env var
- Graceful ImportError handling for Mem0 and Qdrant (log warning, continue without)

### Task 5: Migration 021

- `ALTER TABLE clients ADD COLUMN IF NOT EXISTS mem0_context_id TEXT`
- Links leads to Mem0 research context for vector retrieval

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1-4 | f5969ca | RLMComposer with recursive loop, Mem0, Qdrant, budget caps |
| 5 | d8392eb | Migration 021 — mem0_context_id on clients table |

## Decisions Made

1. **Single module pattern**: All RLM code in `rlm_composer.py` for cohesion (matches `anti_slop.py` pattern)
2. **Graceful degradation**: Mem0/Qdrant unavailable = log warning + continue without (no crash)
3. **Budget 720h window**: Monthly cap uses same approach as Phase 4 middleware budget check
4. **Best-of-N selection**: Returns highest `_composite_score` version, not most recent iteration
5. **Dimension-targeted expansion**: Weakest dimension drives context expansion strategy

## Deviations from Plan

None — plan executed exactly as written. Tasks 1-4 were implemented as a single cohesive module (natural code organization) rather than 4 separate commits, since all functions are in one file and interdependent.

## Verification

- `ruff check titan/pipeline/rlm_composer.py` — PASSED (clean)
- All imports verified: RLMComposer, store_research_context, _retrieve_context, _expand_context, _check_budget
- Graceful degradation confirmed: Mem0/Qdrant unavailable logs warnings, continues without error
- Feature flag defaults to disabled (ENABLE_RLM not set = off)

## Known Stubs

None — all functions are fully implemented with real logic (no placeholders).

## Self-Check: PASSED

- titan/pipeline/rlm_composer.py: FOUND
- scripts/migrations/021-rlm-context.sql: FOUND
- Commit f5969ca: FOUND
- Commit d8392eb: FOUND
