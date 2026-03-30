---
phase: 2
plan: 02-02
subsystem: anti-slop-quality-gate
tags: [anti-slop, quality, pipeline-integration, testing, feature-flag]
dependency_graph:
  requires: [02-01]
  provides: [email-quality-gate, site-copy-quality-gate, anti-slop-tests]
  affects: [titan-pipeline, clawdbot-site-builder]
tech_stack:
  added: []
  patterns: [feature-flag-gating, secret-detection, rewrite-loop, density-scoring]
key_files:
  created:
    - tests/shared/test_anti_slop.py
    - tests/titan/test_email_quality_gate.py
    - tests/titan/__init__.py
  modified:
    - titan/pipeline/email_compose.py
    - clawdbot/site_builder.py
decisions:
  - Secret detection is a hard block (never sends content with secrets)
  - Site copy gate scores each text section individually, records aggregate
  - Rewrite loop runs on body only for emails (subject is short)
  - Site builder gate placed between final HTML and deploy phase
metrics:
  duration: 8min
  completed: 2026-03-29
  tasks: 3/3
  tests_added: 50
  tests_passing: 50
---

# Phase 2 Plan 02-02: Pipeline Integration + Tests Summary

Anti-slop quality gate wired into Titan email and ClawdBot site pipelines with 50-test suite covering scoring, rewrites, secrets, and pipeline integration.

## Task Summary

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Email pipeline quality gate | b551f33 | titan/pipeline/email_compose.py |
| 2 | ClawdBot site copy gate | b881f5f | clawdbot/site_builder.py |
| 3 | Full test suite | 225eb2c | tests/shared/test_anti_slop.py, tests/titan/test_email_quality_gate.py |

## What Was Done

### Task 1: Email Pipeline Quality Gate
- Added `_anti_slop_gate()` helper to email_compose.py
- Checks `ENABLE_ANTI_SLOP` flag first (zero change when off)
- Runs `detect_secrets()` on combined subject+body (hard block on match)
- Scores body with `AntiSlopScorer(context="email")`
- Triggers `rewrite_loop()` if below threshold
- Records quality scores via `record_quality_score()`
- Wired into both `_compose_with_skill()` and `_compose_one()` after existing content validation

### Task 2: ClawdBot Site Copy Gate
- Added `_extract_text_sections()` to pull visible text from HTML (strips tags, splits sections)
- Added `_anti_slop_site_gate()` for per-section scoring and rewriting
- Runs secret detection on full HTML (hard block)
- Scores each text section individually with `context="site_copy"`
- Rewrites sections below threshold via `rewrite_loop()`
- Records aggregate quality scores across all sections
- Gate placed between final HTML generation and deploy phase

### Task 3: Test Suite (50 tests)
- **Protocol compliance** (3): AntiSlopScorer implements SlopScorer Protocol
- **Feature flag** (4): enabled/disabled states with env var
- **Slop scoring** (5): clean/sloppy content, empty, density-based
- **Pattern corpus** (1): 20-sample corpus with 80%+ detection rate
- **Composite scores** (3): perfect, worst, mixed calculations
- **Thresholds** (4): email, site_copy, slop blocking, unknown context
- **Secret detection** (14): API keys, tokens, passwords, CC, SSN, JWT, private keys, DB strings
- **Scoring mocks** (5): 5-dimension return, regex slop, Haiku model check
- **Rewrite loop** (2): best-of-N selection, skip when good enough
- **DB functions** (2): record with correct params, failure logging
- **Pipeline integration** (8): flag on/off, secret blocking, clean passthrough, rewrite trigger, score recording

## Deviations from Plan

None -- plan executed exactly as written.

## Acceptance Criteria

- [x] Email pipeline gated when ENABLE_ANTI_SLOP=true
- [x] Site copy gated before deploy
- [x] 80%+ slop detection rate on 20-sample corpus (100% achieved)
- [x] Secret detection blocks content
- [x] All 50 tests pass
- [x] `ruff check` clean on all modified files

## Known Stubs

None -- all functions are fully wired with real implementations from 02-01.

## Self-Check: PASSED

All 3 created files exist. All 3 commit hashes verified in git log.
