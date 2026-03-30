---
phase: 08-tribe-v2-neuro-scorer
verified: 2026-03-30T02:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
gaps: []
human_verification:
  - test: "Enable ENABLE_NEURO_SCORER=true and send a test email through the pipeline"
    expected: "Email is scored on 4 dimensions, neuro_scores JSONB populated in email_sequences"
    why_human: "Requires running Titan pipeline against live Postgres with email_sequences table"
  - test: "Run 101+ emails through pipeline and verify gating activates"
    expected: "After 100 log-only emails, email 101 with composite < 0.40 is re-drafted with dimension guidance"
    why_human: "Requires live pipeline execution with enough volume to exit log-only phase"
---

# Phase 8: TRIBE v2 Neuro-Scorer Verification Report

**Phase Goal:** Predict neural activation across 4 cognitive dimensions for every outbound email/site/message. Gate low-scoring drafts. Build closed-loop learning.
**Verified:** 2026-03-30T02:00:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | 4 cognitive dimensions scored (self-relevance, trust, cognitive ease, emotional resonance) | VERIFIED | `NeuroScorer.score()` returns `NeuroScores` dataclass with all 4 dims + composite. ROIExtractor maps Desikan-Killiany atlas regions. 26 tests confirm. |
| 2 | Lazy model loading: TRIBE v2 unloaded when not scoring, loaded on demand | VERIFIED | `TribeService` singleton with `_load_model()` on first call, `unload()` frees memory + gc.collect + MPS cache clear. `_TORCH_AVAILABLE`/`_TRIBE_AVAILABLE` flags. Tests confirm singleton + unload. |
| 3 | ROI extraction maps fsaverage5 atlas regions to 4 dimensions | VERIFIED | `DIMENSION_ROIS` maps 15 brain regions across 4 dims. `SYNTHETIC_ATLAS` fallback built at import time. `extract_scores()` aggregates vertex activations and inverts cognitive_load to cognitive_ease. |
| 4 | Neural gating: low-scoring emails re-drafted with dimension-specific guidance | VERIFIED | `_neuro_score_and_gate()` in email_compose.py: 100-email log-only warmup, composite < 0.40 triggers re-draft, `NEURAL_GUIDANCE` dict provides per-dimension prompts, max 2 re-drafts keeping best. Wired into all 3 compose paths (_compose_one, _compose_with_skill, _compose_with_rlm). |
| 5 | Closed-loop: dimension-outcome correlations computed daily after 50+ scored emails | VERIFIED | `neural_reflection()` queries email_sequences JOIN clients, computes Pearson r per dimension vs conversion. Returns early if < 50 rows. Scheduled via Perseus at 86400s interval. |
| 6 | Neural rules auto-extracted when significance reached (p < 0.05, n >= 30) | VERIFIED | `_create_neural_rule()` inserts into titan_rules at p < 0.05, n >= 30. Called from `neural_reflection()`. Tests confirm rule creation at significance threshold. |
| 7 | Segment-specific neural profiles computed after 100+ emails per segment | VERIFIED | `compute_segment_profiles()` queries with `HAVING COUNT(*) >= 100`, groups by industry, stores in titan_learnings. Scheduled weekly (604800s). |
| 8 | All content types scored: emails, site copy, follow-ups, alerts | VERIFIED | `neuro_scorer_middleware` scores stages in `_NEURO_CONTENT_STAGES = {"email_compose", "site_build", "follow_up", "alert_compose"}`. Middleware registered in PIPELINE_CONFIGS for titan, clawdbot, hermes. |
| 9 | Feature flag ENABLE_NEURO_SCORER gates everything | VERIFIED | `is_enabled()` checks env var in neuro_scorer.py; `_is_neuro_enabled()` in email_compose.py; `_flag("ENABLE_NEURO_SCORER")` in middleware. All paths return passthrough when disabled. 4 feature-flag tests confirm. |

**Score:** 9/9 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `titan/neuro/__init__.py` | Package init | VERIFIED | 10 lines, docstring + exports |
| `titan/neuro/tribe_service.py` | Lazy load TRIBE v2 + fallback | VERIFIED | 234 lines. Singleton, keyword heuristics, Haiku LLM fallback, MPS device support |
| `titan/neuro/roi_extractor.py` | Desikan-Killiany ROI mapping | VERIFIED | 178 lines. 15 brain regions, synthetic atlas, cognitive_load inversion |
| `titan/neuro/normalizer.py` | Running percentile normalization | VERIFIED | 96 lines. 50-sample baseline, warm-start sigmoid, scipy optional |
| `titan/neuro/neuro_scorer.py` | Orchestrator producing NeuroScores | VERIFIED | 141 lines. Full pipeline, configurable weights, DB-backed learned weights |
| `titan/neuro/learning_loop.py` | Daily reflection + rules + segments | VERIFIED | 292 lines. Pearson correlation, p-value approximation, weight update, segment profiles |
| `scripts/migrations/024-neuro-scores.sql` | JSONB column + indexes | VERIFIED | 27 lines. ALTER TABLE, composite index, inference_mode index |
| `scripts/tribe-validation-spike.py` | Validation spike doc | VERIFIED | Exists (documented dry-run since TRIBE v2 not installed) |
| `titan/pipeline/email_compose.py` | Neural gating integration | VERIFIED | NEURAL_GUIDANCE dict, _neuro_score_and_gate helper, wired into 3 compose paths |
| `shared/middleware.py` | neuro_scorer_middleware | VERIFIED | Middleware function, registered in MIDDLEWARE_REGISTRY, in all 3 PIPELINE_CONFIGS |
| `perseus/scheduler.py` | Scheduler jobs | VERIFIED | neural_reflection (daily 86400s), segment_profiles (weekly 604800s), non-skippable |
| `tests/titan/test_neuro_scorer.py` | Core + integration tests | VERIFIED | 490 lines, 37 tests, all pass |
| `tests/titan/test_neuro_learning.py` | Learning loop tests | VERIFIED | 403 lines, 21 tests, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| email_compose.py | neuro_scorer.py | `from titan.neuro.neuro_scorer import NeuroScorer` | WIRED | Import inside `_neuro_score_and_gate()`, scorer instantiated and `.score()` called |
| neuro_scorer.py | tribe_service.py | `from titan.neuro.tribe_service import TribeService` | WIRED | Top-level import, `TribeService.get()` called in `score()` |
| neuro_scorer.py | roi_extractor.py | `from titan.neuro.roi_extractor import ROIExtractor` | WIRED | Top-level import, `extract_scores()` called in `score()` |
| neuro_scorer.py | normalizer.py | `from titan.neuro.normalizer import RunningNormalizer` | WIRED | Top-level import, shared instance, `normalize()` called in `score()` |
| middleware.py | neuro_scorer.py | `from titan.neuro.neuro_scorer import NeuroScorer` | WIRED | Import inside middleware, `.score()` called on result output |
| learning_loop.py | shared/db.py | `from shared.db import fetch_all, execute, set_config` | WIRED | DB queries for reflection, rule creation, weight persistence, segment profiles |
| scheduler.py | learning_loop.py | Schedule entries | WIRED | "neural_reflection" and "segment_profiles" entries reference learning pipeline stage |
| email_compose.py | shared/db.py | `UPDATE email_sequences SET neuro_scores` | WIRED | JSONB stored after scoring and after re-draft |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| neuro_scorer.py | NeuroScores | TribeService -> ROIExtractor -> Normalizer | Yes (keyword heuristics fallback produces real float scores) | FLOWING |
| email_compose.py | neuro (NeuroScores) | NeuroScorer.score() | Yes, stored to DB and used for gating decisions | FLOWING |
| middleware.py | neuro_scores dict | NeuroScorer.score() | Yes, attached to pipeline result | FLOWING |
| learning_loop.py | correlations | DB query -> pearsonr | Yes, real statistical computation from scored email data | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 58 tests pass | `pytest tests/titan/test_neuro_scorer.py tests/titan/test_neuro_learning.py` | 58 passed, 0.24s | PASS |
| Ruff lint clean | `ruff check titan/neuro/ shared/middleware.py` | All checks passed | PASS |
| Modules import without errors | Verified via test collection (58 tests collected successfully) | No import errors | PASS |
| Feature flag disabled by default | TestFeatureFlag::test_disabled_by_default | PASSED | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-----------|-------------|--------|----------|
| NEURO-01 | 08-01 | MPS validation spike | SATISFIED | `scripts/tribe-validation-spike.py` exists, documents fallback decision |
| NEURO-02 | 08-01 | TRIBE v2 lazy load/unload | SATISFIED | `TribeService` singleton with `_load_model()`, `unload()`, gc.collect |
| NEURO-03 | 08-01 | ROI extraction with atlas mapping | SATISFIED | `ROIExtractor` with 15 Desikan-Killiany regions across 4 dimensions |
| NEURO-04 | 08-01 | 4-dimension scoring with normalization | SATISFIED | `NeuroScorer.score()` returns all 4 dims normalized via `RunningNormalizer` |
| NEURO-05 | 08-02 | Email pipeline integration with gating | SATISFIED | `_neuro_score_and_gate()` wired into all 3 compose paths |
| NEURO-06 | 08-02 | All content types scored | SATISFIED | `neuro_scorer_middleware` for email, site, follow-up, alert stages |
| NEURO-07 | 08-02 | neuro_scores JSONB storage | SATISFIED | Migration 024, UPDATE in email_compose.py, indexes created |
| NEURO-08 | 08-01 | Closed-loop learning | SATISFIED | `neural_reflection()` with Pearson correlation, scheduled daily |
| NEURO-09 | 08-01 | Neural rule auto-extraction | SATISFIED | `_create_neural_rule()` at p < 0.05, n >= 30, inserts into titan_rules |
| NEURO-10 | 08-01 | Segment-specific neural profiles | SATISFIED | `compute_segment_profiles()` with HAVING >= 100, scheduled weekly |

**Note:** NEURO-05, NEURO-06, NEURO-07 are still marked `[ ]` in REQUIREMENTS.md despite being fully implemented. This is a documentation oversight, not a code gap.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No TODOs, FIXMEs, placeholders, or stub patterns found in any phase 8 file |

### Human Verification Required

### 1. Live Pipeline Email Scoring

**Test:** Enable `ENABLE_NEURO_SCORER=true`, start Titan, send a test email through the pipeline
**Expected:** Email scored on 4 dimensions, neuro_scores JSONB populated in email_sequences row
**Why human:** Requires running Titan pipeline with live Postgres and email_sequences table

### 2. Neural Gating Activation After 100 Emails

**Test:** Score 101+ emails through pipeline, submit one with low quality text
**Expected:** After 100 log-only emails, email 101 with composite < 0.40 triggers re-draft with dimension-specific guidance
**Why human:** Requires live pipeline execution with enough volume to exit log-only warmup phase

### 3. Daily Reflection Execution

**Test:** Run `neural_reflection()` against Postgres with 50+ scored emails that have conversion outcomes
**Expected:** Pearson correlations computed per dimension, rules created at p < 0.05
**Why human:** Requires populated database with real scored email + conversion data

### Gaps Summary

No gaps found. All 9 observable truths verified through code inspection and test execution. All 10 requirements (NEURO-01 through NEURO-10) are satisfied with substantive implementations. All key links are wired. All 58 tests pass. No anti-patterns detected.

The implementation uses graceful degradation throughout (TRIBE v2/torch/nibabel/scipy all optional with fallbacks), which is the correct approach given the runtime environment. The feature flag `ENABLE_NEURO_SCORER` properly gates all paths.

---

_Verified: 2026-03-30T02:00:00Z_
_Verifier: Claude (gsd-verifier)_
