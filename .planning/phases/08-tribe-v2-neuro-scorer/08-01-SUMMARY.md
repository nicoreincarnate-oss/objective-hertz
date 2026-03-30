---
phase: 08-tribe-v2-neuro-scorer
plan: 01
subsystem: titan-neuro
tags: [tribe-v2, neuro-scorer, brain-encoding, cognitive-dimensions, learning-loop]

requires:
  - phase: 02-anti-slop-quality-gate
    provides: quality scoring infrastructure
  - phase: 04-deerflow-middleware-chain
    provides: middleware chain for pipeline integration

provides:
  - TribeService with lazy load/unload and Haiku fallback
  - ROIExtractor with Desikan-Killiany atlas mappings
  - RunningNormalizer with warm-start percentile normalization
  - NeuroScorer orchestrator producing 4-dimension cognitive scores
  - Learning loop with daily reflection, rule extraction, segment profiles
  - Migration 024 for neuro_scores JSONB column

affects: [08-02 pipeline integration, titan email compose, perseus scheduler]

tech-stack:
  added: [numpy (percentile fallback), scipy.stats (optional)]
  patterns: [graceful-degradation, synthetic-atlas-fallback, keyword-heuristic-scoring]

key-files:
  created:
    - titan/neuro/__init__.py
    - titan/neuro/tribe_service.py
    - titan/neuro/roi_extractor.py
    - titan/neuro/normalizer.py
    - titan/neuro/neuro_scorer.py
    - titan/neuro/learning_loop.py
    - scripts/migrations/024-neuro-scores.sql
    - scripts/tribe-validation-spike.py
  modified: []

key-decisions:
  - "TRIBE v2 not installed -- all code uses Haiku-based text-analysis fallback with keyword heuristics"
  - "scipy optional -- numpy-only fallback for percentileofscore and pearsonr"
  - "Synthetic atlas maps 4 dimension columns for fallback activation arrays"
  - "Shared RunningNormalizer instance across NeuroScorer calls for running percentile"
  - "NaN guard in pearsonr fallback for constant-value dimensions"

patterns-established:
  - "Graceful degradation: try import torch/tribev2/nibabel/scipy, set _AVAILABLE flag, fallback"
  - "Synthetic atlas: SYNTHETIC_ATLAS maps region names to column indices for fallback"
  - "Keyword heuristic scoring: _keyword_score() for pure-Python text analysis"

metrics:
  duration: 7min
  completed: "2026-03-30T01:27:00Z"
  tasks_completed: 6
  tasks_total: 6
  files_created: 8
  files_modified: 0
---

# Phase 08 Plan 01: Neuro-Scorer Core + ROI + Learning Summary

TRIBE v2 neuro-scorer core with 4-dimension cognitive scoring, Desikan-Killiany ROI extraction, running percentile normalization, and closed-loop learning -- all with graceful fallback when torch/tribev2/nibabel/scipy unavailable.

## What Was Built

### Task 1: Validation Spike Script
`scripts/tribe-validation-spike.py` -- documents MPS benchmark procedure and decision thresholds. Runs in dry-run mode since torch/tribev2 not installed. Decision gate: FAIL -> Haiku proxy scorer fallback active.

### Task 2: TribeService with Lazy Load
`titan/neuro/tribe_service.py` -- singleton with lazy model loading. When TRIBE v2 unavailable, falls back to Haiku LLM-based proxy scorer, then to pure keyword heuristics. Keyword scoring analyzes self-relevance (personal pronouns, business terms), trust (specificity words), cognitive load (jargon), and emotional resonance (aspiration/pain words).

### Task 3: ROIExtractor + RunningNormalizer
`titan/neuro/roi_extractor.py` -- Desikan-Killiany atlas region mappings for all 4 dimensions (15 brain regions across self-relevance, trust, cognitive load, emotional resonance). Synthetic atlas fallback when nibabel unavailable. Cognitive load inverted to cognitive ease.

`titan/neuro/normalizer.py` -- running percentile normalization after 50-sample baseline. Warm-start sigmoid mapping for pre-baseline scores (centered at 0.5). scipy optional with numpy fallback.

### Task 4: NeuroScorer Orchestrator
`titan/neuro/neuro_scorer.py` -- orchestrates full pipeline: text -> TribeService -> ROIExtractor -> RunningNormalizer -> NeuroScores dataclass. Configurable dimension weights (default: self_relevance=0.30, trust=0.25, cognitive_ease=0.20, emotional_resonance=0.25). Learned weights from system_config DB.

### Task 5: Learning Loop
`titan/neuro/learning_loop.py` -- neural_reflection() computes daily Pearson correlations per dimension vs binary conversion outcome. Auto-creates titan_rules at p<0.05, n>=30. Updates composite weights toward predictive dimensions. compute_segment_profiles() builds weekly per-industry neural signatures for compose prompt injection.

### Task 6: Migration 024
`scripts/migrations/024-neuro-scores.sql` -- adds neuro_scores JSONB column to email_sequences with indexes on composite score and inference mode.

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | b22e3f6 | feat(08-01): add TRIBE v2 validation spike script |
| 2 | 22874d7 | feat(08-01): add TribeService with lazy load/unload and Haiku fallback |
| 3 | 8928411 | feat(08-01): add ROIExtractor + RunningNormalizer with atlas mappings |
| 4 | 4bd5f41 | feat(08-01): add NeuroScorer orchestrator with 4-dimension scoring |
| 5 | 873288f | feat(08-01): add closed-loop learning -- reflection + rules + segments |
| 6 | b9a43a8 | feat(08-01): add migration 024 -- neuro_scores JSONB on email_sequences |
| fix | 3b4a7a4 | fix(08-01): add scipy graceful fallback + fix ruff lint errors |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] scipy.stats not installed**
- **Found during:** Verification (post Task 6)
- **Issue:** scipy not available in runtime environment; normalizer and learning_loop had hard imports
- **Fix:** Added graceful fallback: numpy-only percentileofscore in normalizer, numpy-only pearsonr with t-distribution p-value approximation in learning_loop
- **Files modified:** titan/neuro/normalizer.py, titan/neuro/learning_loop.py
- **Commit:** 3b4a7a4

**2. [Rule 1 - Bug] NaN in pearsonr for constant-value input**
- **Found during:** Functional verification test
- **Issue:** numpy corrcoef returns NaN when one array has zero variance (constant values)
- **Fix:** Added NaN guard returning (0.0, 1.0) for NaN correlation
- **Files modified:** titan/neuro/learning_loop.py
- **Commit:** 3b4a7a4

**3. [Rule 1 - Bug] Ruff F401: unused imports**
- **Found during:** ruff check
- **Issue:** numpy unused in learning_loop.py, TYPE_CHECKING imports unused in tribe_service.py
- **Fix:** Removed unused numpy import, replaced TYPE_CHECKING block with pass
- **Files modified:** titan/neuro/learning_loop.py, titan/neuro/tribe_service.py
- **Commit:** 3b4a7a4

## Known Stubs

None -- all code is fully functional with fallback paths wired.

## Verification

- ruff check titan/neuro/: All checks passed
- All 8 modules import without errors
- Functional test: keyword scoring, ROI extraction, normalization, correlation computation all produce correct output
- TRIBE v2 unavailable -> falls back to keyword heuristics transparently

## Self-Check: PASSED

- All 8 created files exist on disk
- All 7 commit hashes verified in git log
- ruff check clean
- All modules import without errors
