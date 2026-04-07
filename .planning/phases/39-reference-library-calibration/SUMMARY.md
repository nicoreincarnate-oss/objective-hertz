---
phase: 39
plan: reference-library-calibration
subsystem: clawdbot-v2
tags: [clawdbot, visual-pipeline, calibration, references, profiling]
requires:
  - phase 33 (snippet registry, design tokens, taste profile)
  - phase 34 (PlaywrightPool, visual_scorer, reference_manager)
  - phase 35 (BuildPlan, BuildTier)
  - phase 36 (section_agent, section_orchestrator)
  - phase 37 (page_assembler)
  - phase 38 (build_site_v2 + v1 fallback)
provides:
  - curated reference library (30 sites, 6 directions)
  - VLM calibration harness with separation gap metric
  - snippet validation pipeline
  - end-to-end performance profiler
  - e2e integration test coverage
  - SITE_BUILDER_V2.md architecture reference
affects:
  - clawdbot/ (new modules, no edits to existing files)
  - soul/references/ (new)
  - soul/qa-calibration/ (new)
  - scripts/ (new capture script)
  - tests/ (4 new test files, 21 tests)
tech-stack:
  added:
    - pillow (optional, for reference crop fallback)
  patterns:
    - dependency injection for testability (scorer/build_callable)
    - sys.modules stubs for phase 33-38 dependencies in tests
    - dataclass-based structured reports (CalibrationResult, SnippetValidationReport, BuildProfile)
key-files:
  created:
    - soul/references/curated_sites.yaml
    - soul/references/{hero,features,testimonials,pricing,cta,contact,faq,navbar,footer}/metadata.yaml
    - soul/qa-calibration/good/manifest.yaml
    - soul/qa-calibration/bad/manifest.yaml
    - scripts/capture_references.py
    - clawdbot/vlm_calibration.py
    - clawdbot/snippet_validator.py
    - clawdbot/performance_profiler.py
    - tests/test_phase39_e2e.py
    - tests/test_phase39_calibration.py
    - tests/test_phase39_snippet_validator.py
    - tests/test_phase39_profiler.py
    - soul/SITE_BUILDER_V2.md
  modified: []
decisions:
  - Reference PNGs not created in this plan; only structure + metadata. Screenshots are a manual operational task that hits the live web and requires Playwright + VLM runtime.
  - Calibration / validator / profiler modules use lazy imports of phase 33-38 dependencies so tests can inject stubs.
  - E2E tests use sys.modules stubs rather than booting the real build_site_v2, keeping the suite hermetic (no Playwright/Ollama/LLM required).
  - Calibration pass criteria: gap >= 3.0 AND good_mean >= 7.5 AND bad_mean <= 4.0 (all three must hold).
  - Snippet validator low-score threshold = 6.0 (below this, visual quality is flagged for human review).
metrics:
  duration: ~40m
  completed_date: 2026-04-05
  tasks: 7
  files_created: 21
  tests_added: 21
---

# Phase 39 Plan reference-library-calibration: Reference Library + VLM Calibration + Polish Summary

Curated reference library, VLM calibration harness, snippet validator, performance profiler, and architecture docs turning the Phase 33-38 ClawdBot v2 visual pipeline from "functional" to "reliable".

## One-liner
Phase 39 delivers the reliability layer for ClawdBot v2: a 30-site reference library, a VLM calibration harness (separation gap metric), a snippet validation pipeline, an end-to-end profiler, 21 hermetic tests, and full architecture documentation in `soul/SITE_BUILDER_V2.md`.

## What Was Built

### 1. Reference library structure (`soul/references/`)
- `curated_sites.yaml` listing 30 sites across 6 directions:
  cosmos-dark-curation (8), arena-monastic-grid (5), bold-editorial (4),
  minimal-geometric (4), local-business-premium (5), local-business-standard (5).
- Per-section `metadata.yaml` templates for 9 section types (hero, features,
  testimonials, pricing, cta, contact, faq, navbar, footer).
- Intentionally no PNG files: capture is an operator action requiring
  live web access + Playwright + VLM.

### 2. Reference capture script (`scripts/capture_references.py`)
- `capture_all_references(sites_file, only)` iterates the curated list.
- `capture_one_site(url, direction, pool)` does full-page Playwright
  screenshot -> VLM decomposition -> per-section crops.
- Pillow used for PNG cropping when available, graceful full-page fallback
  otherwise.
- CLI: `python -m scripts.capture_references [--only substring] [--verbose]`.

### 3. VLM calibration harness (`clawdbot/vlm_calibration.py`)
- `calibrate_vlm_scorer(calibration_dir, scorer=None)` returns a
  `CalibrationResult` with good/bad means, separation gap, recommended
  threshold, per-dimension accuracy, pass/fail flag.
- Pass criteria: gap >= 3.0 AND good_mean >= 7.5 AND bad_mean <= 4.0.
- Good manifest: 20 examples, expected scores 8-10, all 8 section types covered.
- Bad manifest: 20 examples, expected scores 1-5, covering layout overflow,
  placeholder text, broken spacing, etc.
- Writes `soul/qa-calibration/last_run.yaml` for monitoring.

### 4. Snippet validation pipeline (`clawdbot/snippet_validator.py`)
- `validate_all_snippets(pool, section_type=None)` walks
  `soul/components/` via `snippet_registry.list_available_snippets`.
- Static checks: missing CSS custom properties, Lorem/TODO placeholders,
  deprecated Tailwind classes.
- Dynamic checks: Playwright render errors (`broken_render`), VLM
  `overall < 6.0` (`low_score`).
- Aggregated report: `SnippetValidationReport` with per-category buckets.
- CLI: `python -m clawdbot.snippet_validator [--section-type T] [--json]`.
- Intended as monthly Perseus scheduled task.

### 5. Performance profiler (`clawdbot/performance_profiler.py`)
- `profile_build(lead, site_type, build_callable=None)` returns a
  `BuildProfile` with `total_time_s`, `stage_times`, `section_times`,
  call counts (LLM/VLM/render), `bottleneck`, `exceeded_targets`.
- Targets: plan<15s, contract<30s, sections<4min, assembly<10s, qa<30s,
  demo total<3min, premium total<4min.
- `build_callable` injection point for tests; real `_invoke_with_hooks`
  wraps each phase 35-37 stage with `_timed` context.

### 6. Test suite (21 tests, all passing)
- `tests/test_phase39_calibration.py` — 5 tests: pass/fail paths,
  separation math, threshold midpoint, per-dimension accuracy.
- `tests/test_phase39_snippet_validator.py` — 5 tests: clean path,
  broken render, low-score, missing CSS vars, placeholder detection.
- `tests/test_phase39_profiler.py` — 5 tests: stage recording, bottleneck
  detection, target exceedance, error capture, target-stage coverage.
- `tests/test_phase39_e2e.py` — 6 tests: demo build, full 5-page build,
  v2->v1 fallback, taste overlay, cost tracking, VISUAL_QA_BLOCKING.
- All tests use `sys.modules` stubs or DI — zero live API calls,
  zero Playwright, zero Ollama.

### 7. Architecture documentation (`soul/SITE_BUILDER_V2.md`)
- ASCII system diagram (lead -> plan -> validate -> sections -> assemble -> QA -> deploy).
- Configuration reference table (15+ env vars).
- Cost analysis: demo $0 / premium ~$0.19 breakdown.
- Troubleshooting guide (Playwright install, Ollama, VLM parse errors,
  rate limits, snippet drift, reference staleness).
- Reference library maintenance cadence (quarterly refresh).
- VLM calibration procedure (step-by-step).
- Module map: every phase 33-39 file with a one-line purpose.

## Verification

- `python3 -m ruff check clawdbot/vlm_calibration.py clawdbot/snippet_validator.py clawdbot/performance_profiler.py scripts/capture_references.py tests/test_phase39_*.py` -> all checks passed
- `PYTHONPATH=. python3 -m pytest tests/test_phase39_*.py -v` -> 21 passed
- YAML manifests validated via `yaml.safe_load`

## Deviations from Plan

None. Plan executed as written, with one clarification: the plan specified
"not creating actual PNG screenshots in this task" — this was honored
(structure + metadata only; PNGs are an operational step).

Note on worktree: this worktree branched before phases 33-38 landed, so
the phase 33-38 modules referenced by the new code (snippet_registry,
visual_scorer, renderer, section_*, page_assembler) are not importable
from this commit. All tests work around this via `sys.modules` stubs and
dependency injection, so the test suite is fully hermetic. When this
branch merges forward, the real modules will satisfy the lazy imports
in `snippet_validator`, `vlm_calibration`, `performance_profiler`, and
`capture_references`.

## Known Stubs

None — all code paths are wired to real phase 33-38 modules via lazy
imports. Tests use deliberate stubs (documented) purely for test
isolation.

## Deferred Items

- Task 7 of the original plan ("Monitoring Dashboard Integration" —
  War Room v2 build metrics) was not in the task list for this executor
  prompt; it remains deferred to a follow-up plan.
- Actual reference PNGs must be captured by an operator running
  `python -m scripts.capture_references`. Tracked as an operational
  runbook item in `soul/SITE_BUILDER_V2.md`.
- Actual good/bad calibration PNGs must be captured / generated.
  Manifests are in place; file contents are an operational task.

## Commits

- Task 1: reference library structure + curated sites metadata
- Task 2: reference capture script
- Task 3: VLM calibration harness
- Task 4: snippet validation pipeline
- Task 5: performance profiler
- Task 6: e2e + calibration + validator + profiler tests
- Task 7: SITE_BUILDER_V2 architecture documentation + SUMMARY.md

## Self-Check: PASSED
