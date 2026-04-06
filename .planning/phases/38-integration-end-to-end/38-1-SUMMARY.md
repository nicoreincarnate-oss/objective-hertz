---
phase: "38"
plan: "1"
subsystem: clawdbot
tags: [site-builder, v2-pipeline, integration, visual-production]
dependency_graph:
  requires: [phase-33, phase-34, phase-35, phase-36, phase-37]
  provides: [v2-build-pipeline, feature-flag-routing, playwright-pool-lifecycle]
  affects: [clawdbot/site_builder.py, clawdbot/daemon.py, clawdbot/brain.py, clawdbot/a2a_server.py]
tech_stack:
  added: []
  patterns: [feature-flag-routing, lazy-imports, fallback-with-guard, pool-injection]
key_files:
  created:
    - tests/test_phase38_integration.py
  modified:
    - clawdbot/site_builder.py
    - clawdbot/daemon.py
    - clawdbot/brain.py
    - clawdbot/a2a_server.py
decisions:
  - "Used _v1_fallback parameter guard to prevent v2->v1->v2 recursion instead of renaming functions"
  - "All phase 33-37 imports are lazy (inside function body) to avoid ImportError when modules not yet available"
  - "Event emission wrapped in try/except for best-effort telemetry (DB may not be running)"
  - "Skipped anti_slop_site_gate from plan — function does not exist in codebase"
  - "deploy.py ImportError handled gracefully with fallback to v0 deploy for multipage"
metrics:
  duration_s: 389
  completed: "2026-04-06T14:31:22Z"
  tasks_completed: 9
  tasks_total: 9
  tests_added: 9
  tests_passing: 9
  files_modified: 4
  files_created: 1
---

# Phase 38 Plan 1: Integration End-to-End Summary

V2 visual production pipeline wired into ClawdBot with feature-flag routing, Playwright pool lifecycle, capability advertisement, and full fallback safety.

## What Was Done

### Task 1: _build_site_v2 Function
Added the complete V2 multi-agent visual production pipeline to `clawdbot/site_builder.py`:
- `_build_site()` routes to v2 or v1 based on `CLAWDBOT_V2_ENABLED` env var
- `_build_site_v2()` orchestrates: section planning -> design validation -> parallel build -> assembly -> QA -> deploy gate -> deploy
- `_build_site_v1()` contains the original 5-agent competitive process (unchanged)
- `_v1_fallback` parameter prevents v2->v1 fallback from recursing back into v2
- All phase 33-37 module imports are lazy (inside function) for graceful degradation

### Task 2: Playwright Pool Lifecycle
Added pool management to `clawdbot/daemon.py`:
- `PlaywrightPool` imported behind try/except (phase 34 module may not exist yet)
- `_start_playwright_pool()` called during daemon startup when V2 enabled
- `_stop_playwright_pool()` called during daemon shutdown
- Pool injected into site_builder via `_set_playwright_pool()`

### Tasks 3+4: Brain + A2A Capability Advertisement
- `brain._inventory()` reports V2 module availability when `CLAWDBOT_V2_ENABLED` is set
- A2A card advertises v2 capabilities (`site_build_v2`, `section_plan`, `fullpage_qa`, `visual_score`)
- Version bumped to 2.0.0 when V2 active

### Tasks 5-8: Multi-Page, Events, Fallback, QA Gate
All handled within the `_build_site_v2` implementation:
- Multi-page: `page_count > 1` triggers `assemble_multipage_site` + `deploy_static_site`
- Events: `site_build_v2_started`, `site_build_v2_completed`, `site_build_v2_fallback`
- Fallback: entire v2 flow wrapped in try/except, falls back to v1 with guard
- Deploy gate: `deploy_gate()` from fullpage_qa blocks deployment on low QA scores

### Task 9: Integration Tests
9 tests covering all integration paths:
- Flag routing (v1 vs v2)
- Full v2 flow with mocked components
- Fallback on error (no recursion)
- Playwright pool getter/setter
- Event emission verification
- Deploy gate blocking
- Multi-page assembly flow

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] anti_slop_site_gate does not exist**
- **Found during:** Task 1
- **Issue:** Plan referenced `_anti_slop_site_gate()` but no such function exists anywhere in the codebase
- **Fix:** Omitted anti-slop gate call from v2 pipeline; can be added when the function is created
- **Files modified:** clawdbot/site_builder.py

**2. [Rule 3 - Blocking] deploy.py does not exist**
- **Found during:** Task 1
- **Issue:** Plan referenced `clawdbot.deploy.deploy_static_site` but deploy.py doesn't exist
- **Fix:** Wrapped in try/except ImportError with fallback to v0 deploy
- **Files modified:** clawdbot/site_builder.py

**3. [Rule 3 - Blocking] psycopg_pool not available in test env**
- **Found during:** Task 9
- **Issue:** Tests couldn't import site_builder due to transitive psycopg_pool dependency
- **Fix:** Pre-mocked heavy modules (psycopg_pool, shared.db, etc.) at test file top
- **Files modified:** tests/test_phase38_integration.py

## Known Stubs

None. All code paths are either fully wired or have explicit graceful degradation (ImportError handling for modules from phases 33-37 that don't exist yet).

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 | 7254898 | feat(phase38): _build_site_v2 with full visual production pipeline |
| 2 | 203a8cc | feat(phase38): Playwright pool lifecycle in daemon |
| 3+4 | 29cdb7e | feat(phase38): brain + A2A v2 capability advertisement |
| 9 | e80b786 | test(phase38): integration test suite for V2 visual production pipeline |

## Self-Check: PASSED

All 5 modified/created files exist. All 4 commit hashes verified in git log.
