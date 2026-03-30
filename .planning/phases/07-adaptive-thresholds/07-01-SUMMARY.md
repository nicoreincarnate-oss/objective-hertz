---
phase: 07-adaptive-thresholds
plan: 01
subsystem: revenue-engine
tags: [thompson-sampling, beta-bandit, adaptive-thresholds, postgres, numpy, scipy]

# Dependency graph
requires:
  - phase: 04-middleware-chain
    provides: stage_metrics telemetry for training data
  - phase: 02-anti-slop-quality-gate
    provides: quality_scores for evaluation
provides:
  - Thompson sampling BetaBandit for adaptive expansion thresholds
  - DB-persisted bandit state (adaptive_thresholds table)
  - Pipeline outcome training signal collection
  - ExperimentManager for concurrent A/B shadow experiments
  - meta_evaluations audit table for all threshold changes
  - Migration 023 with warm-start Beta(10,2) priors
affects: [07-02-feature-flag, expansion, titan-pipeline]

# Tech tracking
tech-stack:
  added: [numpy (Beta sampling), scipy (credible intervals)]
  patterns: [thompson-sampling-bandit, beta-bernoulli-posterior, deterministic-hashed-assignment]

key-files:
  created:
    - titan/adaptive_thresholds.py
    - scripts/migrations/023-adaptive-thresholds.sql
  modified:
    - perseus/scheduler.py

key-decisions:
  - "Combined sync ThresholdProvider methods + async DB-backed methods in single class for Protocol compliance"
  - "Deterministic lead assignment via SHA-256 hash ensures consistent A/B splits"
  - "ExperimentManager uses Monte Carlo P(variant > control) for statistical evaluation"
  - "Warm-start Beta(10,2) prior washes out after ~50 observations"

patterns-established:
  - "Thompson sampling bandit: BetaBandit.sample() for exploration, update() for exploitation"
  - "meta_evaluations audit: every threshold change logged with reason, confidence, sample_size"
  - "Deterministic experiment assignment: SHA-256(lead_id:exp_id) mod 2 for 50/50 split"

requirements-completed: [ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04, ADAPT-05, ADAPT-06]

# Metrics
duration: 3min
completed: 2026-03-30
---

# Phase 7 Plan 01: Bandit + DB + Experiments + Migration Summary

**Thompson sampling bandits with Beta(10,2) warm-start priors, DB persistence, pipeline outcome training, and concurrent A/B experiments with meta_evaluations audit**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-30T00:57:29Z
- **Completed:** 2026-03-30T01:01:07Z
- **Tasks:** 6 (Tasks 4 and 6 combined with Task 2)
- **Files modified:** 3

## Accomplishments
- BetaBandit with Thompson sampling from Beta distribution (clean-room Sutton & Barto Ch. 2.7)
- AdaptiveThresholds satisfies ThresholdProvider Protocol with sync + async DB-backed methods
- Migration 023 creates adaptive_thresholds and meta_evaluations tables with Beta(10,2) seed
- ExperimentManager supports 2+ concurrent shadow experiments with deterministic 50/50 assignment
- Pipeline outcome training collects lead conversion signals and updates all bandits daily
- Perseus scheduler wired with bandit_training daily job

## Task Commits

Each task was committed atomically:

1. **Task 1: BetaBandit + AdaptiveThresholds** - `d822d7b` (feat)
2. **Task 2: Migration 023 + DB persistence + Task 4 warm-start + Task 6 meta_evaluations** - `504dfe6` (feat)
3. **Task 3: Pipeline outcome training** - `bc5b5fe` (feat)
4. **Task 4: Warm-start priors** - combined with Task 2 (`504dfe6`)
5. **Task 5: ExperimentManager** - included in Task 1 (`d822d7b`) as single module
6. **Task 6: meta_evaluations table** - combined with Task 2 (`504dfe6`)

## Files Created/Modified
- `titan/adaptive_thresholds.py` - BetaBandit, AdaptiveThresholds, ExperimentManager, training signal
- `scripts/migrations/023-adaptive-thresholds.sql` - adaptive_thresholds + meta_evaluations tables
- `perseus/scheduler.py` - Added bandit_training daily schedule

## Decisions Made
- Combined sync ThresholdProvider Protocol methods with async DB-backed variants in single class -- sync methods for tests/in-memory use, async methods for production DB path
- ExperimentManager uses deterministic SHA-256 hashing for consistent lead assignment (not random)
- Monte Carlo estimation (10K draws) for P(variant > control) in experiment evaluation
- Training signal maps all lead outcomes to all thresholds (global conversion signal)
- Feature flag check in run_daily_training() prevents any bandit activity when disabled

## Deviations from Plan

None - plan executed exactly as written. Tasks 4, 5, and 6 were combined with their parent tasks as specified in the plan.

## Issues Encountered
- Ruff flagged 4 style issues (unused import noqa, `not in` syntax, `datetime.UTC` alias, unused loop variable) -- all fixed before commit

## User Setup Required
None - no external service configuration required. Migration 023 runs at next database migration cycle.

## Next Phase Readiness
- Plan 07-02 (feature flag + tests) can proceed -- AdaptiveThresholds ready for expansion.py integration
- ENABLE_BANDIT_EXPANSION feature flag gates all adaptive behavior

## Self-Check: PASSED

---
*Phase: 07-adaptive-thresholds*
*Completed: 2026-03-30*
