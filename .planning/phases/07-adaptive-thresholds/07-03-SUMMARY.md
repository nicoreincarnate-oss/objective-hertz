---
phase: 07-adaptive-thresholds
plan: 03
subsystem: pipeline
tags: [thompson-sampling, backtest, a/b-testing, bandits]

requires:
  - phase: 07-01
    provides: "BetaBandit, AdaptiveThresholds, ExperimentManager"
  - phase: 07-02
    provides: "expansion.py wiring, feature flag integration"
provides:
  - "backtest_adaptive_vs_hardcoded() for replaying historical data through both paths"
  - "Concurrent experiment test coverage for assign_lead and evaluate_experiments"
affects: []

tech-stack:
  added: []
  patterns: ["backtest replay pattern for comparing adaptive vs static strategies"]

key-files:
  created: []
  modified:
    - titan/adaptive_thresholds.py
    - tests/titan/test_adaptive_thresholds.py

key-decisions:
  - "Backtest uses fresh in-memory bandits (no DB) for pure replay comparison"
  - "Hardcoded path normalises defaults by _SCALE to get [0,1] for fair comparison"

patterns-established:
  - "Backtest replay: feed historical (name, outcome) tuples through parallel decision paths"

requirements-completed: [ADAPT-05]

duration: 2min
completed: 2026-03-30
---

# Phase 7 Plan 3: Gap Closure Summary

**Backtest function comparing adaptive bandit vs hardcoded thresholds, plus concurrent experiment test coverage for ExperimentManager**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-30T01:18:01Z
- **Completed:** 2026-03-30T01:19:37Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Added `backtest_adaptive_vs_hardcoded()` function that replays historical outcomes through both adaptive bandit and hardcoded threshold paths
- Adaptive path demonstrated >= hardcoded accuracy on 200-outcome seeded replay (deterministic, seed=42)
- Added 2 concurrent experiment tests proving assign_lead handles 2+ experiments and evaluate_experiments produces independent winners
- All 58 tests pass (53 existing + 5 new), zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Add backtest function + test** - `b140cfe` (feat)
2. **Task 2: Add concurrent experiment tests** - `de223f9` (test)

## Files Created/Modified
- `titan/adaptive_thresholds.py` - Added `backtest_adaptive_vs_hardcoded()` function after `run_daily_training()`
- `tests/titan/test_adaptive_thresholds.py` - Added `TestBacktest` class (3 tests) and 2 concurrent experiment tests in `TestExperimentManager`

## Decisions Made
- Backtest uses fresh in-memory bandits (no DB) for pure replay -- keeps the function synchronous and testable without mocks
- Hardcoded path normalises default values by `_SCALE` to produce [0,1] range for fair boundary comparison against adaptive bandit samples

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 7 verification gaps fully closed (backtest blocker + concurrent experiments warning)
- All 58 adaptive threshold tests passing
- Ready for Phase 8 (TRIBE v2 Neuro-Scorer)

## Self-Check: PASSED

- All files exist (titan/adaptive_thresholds.py, tests/titan/test_adaptive_thresholds.py, 07-03-SUMMARY.md)
- All commits verified (b140cfe, de223f9)
- backtest_adaptive_vs_hardcoded function present in source
- 58/58 tests passing

---
*Phase: 07-adaptive-thresholds*
*Completed: 2026-03-30*
