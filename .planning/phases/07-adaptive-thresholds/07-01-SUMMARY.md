---
phase: 07-adaptive-thresholds
plan: 01
subsystem: pipeline
tags: [thompson-sampling, beta-distribution, numpy, bandits, adaptive-thresholds, a-b-testing]

# Dependency graph
requires:
  - phase: 04-deerflow-middleware
    provides: stage_metrics table (training data source)
  - phase: 02-anti-slop-quality-gate
    provides: quality_scores table (evaluation data)
  - phase: 0b
    provides: ThresholdProvider Protocol contract
provides:
  - Thompson sampling BetaBandit for adaptive thresholds
  - AdaptiveThresholds implementing ThresholdProvider Protocol
  - Pipeline outcome training signal collection
  - ExperimentManager for concurrent A/B shadow experiments
  - Migration 023 (adaptive_thresholds + meta_evaluations tables)
  - Daily training job for Perseus scheduler
affects: [08-tribe-neuro-scorer, titan-expansion, perseus-scheduler]

# Tech tracking
tech-stack:
  added: [numpy (Beta distribution sampling)]
  patterns: [Thompson sampling bandit, warm-start priors, meta-evaluation audit logging]

key-files:
  created:
    - titan/adaptive_thresholds.py
    - scripts/migrations/023-adaptive-thresholds.sql
    - tests/titan/test_adaptive_thresholds.py
  modified: []

key-decisions:
  - "scipy optional (graceful degradation) -- confidence_interval returns None when scipy unavailable"
  - "ExperimentManager stores experiment data in meta_evaluations table (single audit surface)"
  - "Training signal uses clients table status transitions (binary: converted vs lost/stale)"
  - "Warm-start Beta(10,2) means ~50 observations needed before data dominates prior"

patterns-established:
  - "Bandit pattern: BetaBandit dataclass with sample()/update()/mean/confidence_interval"
  - "Async DB pattern: load_from_db/save_to_db/load_all_from_db for bandit persistence"
  - "Meta-evaluation audit: every threshold change logged with reason, confidence, sample_size"

requirements-completed: [ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04, ADAPT-05, ADAPT-06]

# Metrics
duration: 6min
completed: 2026-03-30
---

# Phase 7 Plan 01: Bandit + DB + Experiments + Migration Summary

**Thompson sampling bandits (Sutton & Barto clean-room) with DB persistence, pipeline training signal, concurrent A/B experiments, and meta_evaluations audit table**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-30T00:35:35Z
- **Completed:** 2026-03-30T00:41:23Z
- **Tasks:** 6 (4 commits -- Tasks 4/6 combined with Task 2)
- **Files modified:** 4

## Accomplishments
- BetaBandit with Thompson sampling from Beta distribution (clean-room Sutton & Barto 2018, Ch 2.7)
- AdaptiveThresholds implementing ThresholdProvider Protocol with full DB persistence
- Pipeline outcome training signal from client conversion data (daily job)
- ExperimentManager for concurrent A/B shadow experiments with 50/50 random assignment
- Migration 023 creating adaptive_thresholds + meta_evaluations tables with warm-start seeds
- 41 tests passing covering bandits, DB ops, training, experiments, feature flags, license compliance

## Task Commits

Each task was committed atomically:

1. **Task 1: BetaBandit + AdaptiveThresholds** - `a55920c` (feat)
2. **Task 2+4+6: Migration 023 + DB persistence + warm-start + meta_evaluations** - `9ebdb45` (feat)
3. **Task 3+5: Pipeline training + ExperimentManager** - `563573b` (feat)
4. **Tests: 41 tests + license compliance** - `14b1c3d` (test)

## Files Created/Modified
- `titan/adaptive_thresholds.py` - BetaBandit, AdaptiveThresholds, ExperimentManager, training signal
- `scripts/migrations/023-adaptive-thresholds.sql` - adaptive_thresholds + meta_evaluations tables
- `tests/titan/test_adaptive_thresholds.py` - 41 tests covering all components
- `tests/titan/__init__.py` - Package init
- `shared/contracts.py` - ThresholdProvider Protocol (brought from intel-integration branch)

## Decisions Made
- scipy is optional: confidence_interval returns None when scipy is not installed (numpy suffices for sampling)
- ExperimentManager uses meta_evaluations table for all experiment data (single audit surface, no extra table)
- Training signal uses clients table status transitions as binary signal (converted vs lost/stale/unresponsive)
- Warm-start Beta(10,2) priors: mean=0.833, ~50 observations to wash out prior (avoids cold-start randomness)
- No HyperAgents references in source code (clean-room compliance verified by tests)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Imported shared/contracts.py from intel-integration branch**
- **Found during:** Task 1
- **Issue:** shared/contracts.py with ThresholdProvider Protocol did not exist in this worktree branch
- **Fix:** git checkout from intel-integration branch
- **Files modified:** shared/contracts.py
- **Verification:** isinstance(AdaptiveThresholds(), ThresholdProvider) returns True
- **Committed in:** a55920c (Task 1 commit)

**2. [Rule 1 - Bug] Removed HyperAgents string from docstrings**
- **Found during:** Test writing
- **Issue:** Docstring contained "Zero HyperAgents code" which triggered license compliance test
- **Fix:** Replaced with "Clean-room only" / "Clean-room implementation from textbook only"
- **Files modified:** titan/adaptive_thresholds.py
- **Verification:** License compliance tests pass (41/41)
- **Committed in:** 14b1c3d (test commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered
- scipy not installed on this environment; confidence_interval returns None (acceptable, numpy covers core sampling)
- ruff flagged 5 issues (unused imports, UTC alias, membership test) -- auto-fixed

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Adaptive thresholds module ready for Phase 07-02 (feature flag wiring into expansion.py)
- ENABLE_BANDIT_EXPANSION flag seeded as false in system_config
- All 5 threshold bandits seeded in migration 023

---
*Phase: 07-adaptive-thresholds*
*Completed: 2026-03-30*
