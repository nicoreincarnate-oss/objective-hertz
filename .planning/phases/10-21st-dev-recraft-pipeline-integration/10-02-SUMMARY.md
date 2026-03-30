---
phase: 10-21st-dev-recraft-pipeline-integration
plan: 02
subsystem: clawdbot/site_builder
tags: [pipeline, recraft, 21st-dev, component-enrichment, site-builder]
dependency_graph:
  requires: ["10-01"]
  provides: ["enriched-pipeline", "component-variant-prompts", "recraft-budget-tests"]
  affects: ["clawdbot/site_builder.py", "tests/"]
tech_stack:
  added: []
  patterns: ["async-with-sync-fallback", "component-snippet-injection", "budget-gated-asset-generation"]
key_files:
  created:
    - tests/test_recraft_pipeline.py
    - tests/test_site_quality_comparison.py
  modified:
    - clawdbot/site_builder.py
    - tests/test_build_site_gate.py
decisions:
  - "Async _resolve_design_sources_async wraps enriched call with sync fallback for resilience"
  - "Component snippets extracted from design_sources in build_plan, formatted as COMPONENT PATTERNS block"
  - "design_sources key added to _resolve_build_plan return dict to carry snippets through pipeline"
  - "Component blocks truncated to 1200 chars each to prevent prompt bloat"
metrics:
  duration: 5min
  completed: "2026-03-30T22:25:12Z"
  tasks: 2
  files: 4
  tests_added: 13
  tests_total: 29
---

# Phase 10 Plan 02: Pipeline Integration + Recraft Tests Summary

Enriched 21st.dev component snippets and Recraft asset generation wired into site builder pipeline with async fallback, budget tracking verification, and quality comparison tests proving enriched prompts carry more structural context.

## What Changed

### clawdbot/site_builder.py
- Added `_resolve_design_sources_async()`: async wrapper that calls `resolve_design_sources_with_components()` from design_sources.py, falls back to sync `_resolve_design_sources()` on any error
- Added `_format_component_snippets_block()`: formats component code snippets into a COMPONENT PATTERNS prompt block for variant generation
- Updated `_generate_reference_strategy()`: now calls `await _resolve_design_sources_async(lead)` instead of sync `_resolve_design_sources(lead)`
- Updated `_resolve_build_plan()`: passes `design_sources` through to build_plan dict
- Updated `_build_one_variant()`: extracts component_snippets from build_plan's design_sources, injects COMPONENT PATTERNS block into variant prompt between brief and technical requirements

### tests/test_recraft_pipeline.py (NEW)
7 tests covering:
- `_generate_asset_pack` returns logo+hero when allowed (mock handle_image_generation)
- Budget guard blocks when `allow_paid_assets=False`
- Asset count tracking (partial success)
- Hero generation blocked when `remaining_site_asset_budget < 0.02`
- Recraft API failure is non-fatal
- Async design source resolution delegates to enriched version
- Component snippets formatted correctly for variant prompts

### tests/test_site_quality_comparison.py (NEW)
3 tests proving:
- Component-enriched prompt block is substantially richer than text-only (empty string)
- Enriched block carries real Tailwind patterns (grid-cols, rounded-xl, shadow-sm)
- Empty snippets produce no block (no prompt bloat)

### tests/test_build_site_gate.py (EXTENDED)
3 new tests + 1 existing fix:
- Pipeline degrades gracefully when 21st.dev API unavailable (falls back to text-only)
- Generated asset URLs appear in variant prompt context
- Component snippets from design_sources flow through build_plan to variant prompt
- Fixed existing test stub (added `get_config` to fake shared.db)

## Pipeline Flow (After This Plan)

```
1. _generate_reference_strategy()  --> uses _resolve_design_sources_async (enriched components)
2. _resolve_build_plan()           --> carries design_sources with component_snippets
3. _generate_asset_pack()          --> Recraft logo + hero with budget tracking
4. _build_one_variant() x N        --> COMPONENT PATTERNS block + asset URLs in prompt
5. Opus review                     --> reviews variant quality
6. Synthesis                       --> cherry-picks best elements
7. Anti-slop gate                  --> quality check
8. Inner pages                     --> additional pages
9. Deploy                          --> v0.dev deployment
```

## Commits

| Task | Commit | Message |
|------|--------|---------|
| 1 (RED) | fc6e7e5 | test(10-02): add failing tests for Recraft pipeline and component enrichment |
| 1 (GREEN) | fdf4591 | feat(10-02): wire enriched components + Recraft pipeline into site builder |
| 2 | 55878c5 | test(10-02): add quality comparison + pipeline integration tests |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed existing test stub missing get_config**
- **Found during:** Task 2
- **Issue:** `load_build_site_module()` in `test_build_site_gate.py` did not stub `get_config` on fake `shared.db`, causing `ImportError` when `titan.pipeline.build_site` tried to import it
- **Fix:** Added `fake_db.get_config = AsyncMock(return_value=None)` to the stub
- **Files modified:** tests/test_build_site_gate.py
- **Commit:** 55878c5

## Known Stubs

None. All wiring is functional with real fallback paths. Component enrichment degrades to text-only when 21st.dev API is unavailable. Recraft degrades to empty URLs when budget guard blocks or API fails.

## Verification Results

- 29/29 tests pass across 5 test files
- `resolve_design_sources_with_components` wired in 2 locations in site_builder.py
- `component_snippets` referenced in 3 locations in site_builder.py
- Asset flow (logo_url, hero_url) verified intact
- ruff check passes on all modified files
