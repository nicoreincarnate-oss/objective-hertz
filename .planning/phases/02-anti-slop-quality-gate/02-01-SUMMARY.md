---
phase: 02-anti-slop-quality-gate
plan: "01"
subsystem: quality
tags: [anti-slop, regex, haiku, quality-scoring, secret-detection, postgres]

requires:
  - phase: 0b
    provides: SlopScorer Protocol contract in shared/contracts.py
provides:
  - 5-dimension quality scorer (AntiSlopScorer) implementing SlopScorer Protocol
  - Best-of-N rewrite loop returning highest-scoring version
  - Per-context quality thresholds (email, site_copy, alert, internal)
  - Secret detection with 26 regex patterns
  - quality_scores DB table (migration 018) with trend queries
affects: [02-02-pipeline-integration, titan-email-pipeline, clawdbot-site-builder]

tech-stack:
  added: []
  patterns: [regex-based scoring before LLM scoring, best-of-N selection, feature-flag gating]

key-files:
  created:
    - shared/anti_slop.py
    - scripts/migrations/018-quality-scores.sql
  modified:
    - shared/contracts.py (brought from intel-integration branch)

key-decisions:
  - "Slop score uses density-based normalization (matches per 100 words / 10, capped at 1.0)"
  - "LLM scoring defaults to 0.5 on failure (fail-open for non-blocking quality gate)"
  - "All code written in single module for cohesion -- scorer, rewrite loop, thresholds, secrets, DB functions"

patterns-established:
  - "Feature flag pattern: os.environ.get('ENABLE_ANTI_SLOP', '').lower() in ('true', '1')"
  - "Graceful LLM degradation: defaults returned when Haiku unavailable"
  - "Composite scoring: equal-weight average across 5 dimensions with slop inverted"

requirements-completed: [SLOP-01, SLOP-03, SLOP-04, SLOP-06, SLOP-07, SLOP-08]

duration: 12min
completed: 2026-03-29
---

# Phase 2 Plan 01: Anti-Slop Scorer + Rewrite Loop + Secrets + DB Summary

**Regex-based slop detection (66 patterns) + Haiku LLM quality scoring across 5 dimensions with best-of-N rewrite loop and 26-pattern secret scanner**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-29T21:21:26Z
- **Completed:** 2026-03-29T21:33:03Z
- **Tasks:** 5
- **Files created:** 2 (shared/anti_slop.py, scripts/migrations/018-quality-scores.sql)

## Accomplishments
- AntiSlopScorer implementing SlopScorer Protocol with 5-dimension scoring (clarity, specificity, authenticity, value_density, slop_score)
- 66 regex slop patterns with density-based normalization (sloppy content scores ~1.0, clean content scores ~0.0)
- Best-of-N rewrite loop that returns highest-scoring version across up to 3 iterations
- Per-context thresholds (email=strict 0.7/0.2, site_copy=strictest 0.75/0.15, alert=relaxed 0.5/0.4, internal=very relaxed 0.3/0.6)
- Secret detection with 26 regex patterns covering API keys, AWS creds, tokens, passwords, PII, private keys, connection strings
- Migration 018 creating quality_scores table with 3 indices
- record_quality_score() and get_quality_trend() async DB functions

## Task Commits

Each task was committed atomically:

1. **Task 1: 5-dimension quality scorer** - `f24c64c` (feat)
2. **Tasks 2-5: Rewrite loop + thresholds + secrets + DB migration** - `9506035` (feat)

## Files Created/Modified
- `shared/anti_slop.py` - Complete anti-slop engine: scorer, rewrite loop, thresholds, secret detection, DB functions
- `shared/contracts.py` - SlopScorer Protocol contract (brought from intel-integration branch)
- `scripts/migrations/018-quality-scores.sql` - quality_scores table with 5 dimension columns + indices

## Decisions Made
- Slop score uses density normalization (matches per 100 words / 10) rather than raw count -- handles short and long content equally
- LLM scoring defaults to 0.5 on failure -- fail-open approach so quality gate doesn't block pipeline when Haiku is unavailable
- Combined all anti-slop code in single module (shared/anti_slop.py) for cohesion -- scorer, rewrite, thresholds, secrets, DB all co-located
- contracts.py checked out from intel-integration branch to provide SlopScorer Protocol definition

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Brought contracts.py from intel-integration branch**
- **Found during:** Task 1
- **Issue:** shared/contracts.py with SlopScorer Protocol didn't exist in this worktree (exists on intel-integration branch)
- **Fix:** git checkout intel-integration -- shared/contracts.py
- **Files modified:** shared/contracts.py
- **Verification:** isinstance(AntiSlopScorer(), SlopScorer) passes
- **Committed in:** f24c64c (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential to satisfy the Protocol compliance requirement. No scope creep.

## Issues Encountered
None beyond the contracts.py import resolved above.

## Verification Results
- `ruff check shared/anti_slop.py` -- all checks passed
- `isinstance(AntiSlopScorer(), SlopScorer)` -- True
- Sloppy content scores 1.0, clean content scores 0.0
- 26 secret patterns (>25 requirement)
- 66 slop patterns (>60 requirement)
- Good-enough thresholds correctly gate per context type

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Anti-slop engine ready for pipeline integration (Plan 02-02)
- Migration 018 needs to be applied to Postgres before DB functions work
- Feature flag ENABLE_ANTI_SLOP must be set to activate the gate

---
*Phase: 02-anti-slop-quality-gate*
*Completed: 2026-03-29*
