---
phase: 03
plan: 02
wave: 2
title: Ruflo Aider Loop Wiring (Layer B)
status: SHIP
completed: 2026-04-08
---

# Phase 3 Plan 02 — Ruflo Aider Runtime Wiring SUMMARY

**Verdict: SHIP.** Aider architect+editor loop is now an opt-in runtime path in Ruflo behind `ENABLE_AIDER_LOOPS=false` (default). Legacy `propose_change → shadow_test` path is preserved as the fallback. Behavior unchanged when flag is OFF.

## Task Status

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Create `ruflo/aider_handler.py` | DONE | 95 LoC, wraps `run_ruflo_aider_loop` with TierName.GENIUS architect + TierName.LOCAL editor, max 3 iterations |
| 2 | Wire into `ruflo/agent.py:_handle_code_fix` | DONE | Try Aider first when flag ON; fall back to legacy on `skipped/failed/escalated`; legacy is default when flag OFF |
| 3 | Append `ENABLE_AIDER_LOOPS=false` to `.env.example` | DONE | Phase 3 Wave 2+3 section |
| 4 | Unit tests `tests/test_phase3_ruflo_aider.py` | DONE | 4/4 passing |

## Commits

| SHA | Message |
|-----|---------|
| `2646add` | feat(ruflo): add aider_handler for Phase 3 Wave 2 (opt-in behind ENABLE_AIDER_LOOPS) |
| `361910a` | feat(ruflo): wire aider_handler into agent.dispatch (feature-flagged) |
| `531ee70` | test(phase-3): add Ruflo Aider handler opt-in gate tests |

## Validation Results

1. **AST parse `ruflo/aider_handler.py`** — OK
2. **Import `from ruflo.aider_handler import handle_aider_fix_task, aider_loops_enabled`** — OK
3. **AST parse `ruflo/agent.py`** — OK (post-edit)
4. **Import `ruflo.agent`** — OK
5. **`pytest tests/test_phase3_ruflo_aider.py -v`** — **4 passed in 0.02s**
   - `test_aider_loops_disabled_by_default` PASSED
   - `test_aider_loops_enabled_env_var` PASSED
   - `test_handler_returns_skipped_when_disabled` PASSED
   - `test_handler_requires_failing_test_and_error` PASSED
6. **`pytest tests/ -k ruflo`** — 14 ruflo tests pass + 4 new = 18 passing. 5 failures are **pre-existing** (`shared.observability.bind_context_from_payload` ImportError in `shared/a2a_wrapper.py`, golden-prompt drift in `test_phase44_regression`). None reference `aider_handler`.
7. **`grep ENABLE_AIDER_LOOPS .env.example`** — `ENABLE_AIDER_LOOPS=false` present.

## Preservation Confirmed

- `shared/aider/sandbox_runner.py` default in `ruflo_loop` — untouched (P0-3 preserved)
- `shared/aider/lead_worker.py` LeadWorkerLoop deepcopy — untouched (P1-7 preserved)
- `shared/llm_client.py` — untouched (1711-line canonical)
- `shared/aider/ruflo_loop.py` — untouched (Phase 2 canonical)
- `.env` — untouched
- Existing `_handle_code_fix` legacy path runs unchanged when flag is OFF (default)

## Deferred / Out of Scope

- 5 pre-existing ruflo-related test failures rooted in `shared.observability` import drift and `test_phase44_regression` golden-prompt corpus. Logged for a future Phase 3 cleanup wave; not caused by this plan.
- Full end-to-end Aider loop integration test (architect → editor → verifier with real LLM): covered by Phase 2 `tests/test_phase42_aider_*.py`; this plan only validates the dispatch gate.

## Self-Check: PASSED

- `ruflo/aider_handler.py` — exists
- `ruflo/agent.py` import + branch — present
- `.env.example` — `ENABLE_AIDER_LOOPS=false` present
- `tests/test_phase3_ruflo_aider.py` — exists, 4/4 pass
- Commits `2646add`, `361910a`, `531ee70` — all in `git log`
