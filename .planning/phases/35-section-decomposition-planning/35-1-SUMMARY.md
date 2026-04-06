---
phase: "35"
plan: "1"
subsystem: clawdbot
tags: [section-planner, design-contract, build-tier, content-extraction, vlm-decomposition]
dependency_graph:
  requires: [phase-33-design-tokens, phase-34-visual-qa]
  provides: [section-planner, build-plan-api, industry-templates]
  affects: [clawdbot-site-builder, phase-36-section-generation]
tech_stack:
  added: []
  patterns: [dataclass-driven-planning, tier-based-cost-model, llm-fallback-content-generation]
key_files:
  created:
    - clawdbot/section_planner.py
    - tests/test_phase35_section_planner.py
  modified: []
decisions:
  - "Made site_builder DESIGN_DIRECTIONS import optional to avoid heavy dependency chain in tests"
  - "Used _missing marker pattern for content that needs LLM generation instead of eager generation"
  - "Demo tier essential sections: hero, services/features, contact, footer (always preserved during truncation)"
metrics:
  duration_seconds: 401
  completed: "2026-04-06T04:58:00Z"
  tasks_completed: 7
  tasks_total: 7
  tests_added: 25
  tests_passing: 25
---

# Phase 35 Plan 1: Section Decomposition + Build Planning Summary

Section-aware build planner that turns lead briefs into structured BuildPlans with per-section content, design tokens, industry templates, and cost-tiered iteration budgets.

## What Was Built

### Task 1: Section Plan Generator (`clawdbot/section_planner.py`)
- `SectionPlan` and `BuildPlan` dataclasses for structured build planning
- `generate_build_plan()` async function: loads taste profile, selects direction, extracts tokens, builds section plans with content, snippet refs, and iteration budgets
- Replaces the monolithic `_build_product_brief()` in site_builder.py

### Task 2: Design Contract Generation
- `generate_design_contract()` produces shared context for all section agents
- Includes palette (8 colors), typography (fonts + scale), spacing, nav, footer, motion, brand, and pre-built CSS vars/head/tailwind blocks
- Nav links auto-inferred from section list

### Task 3: Content Extraction from Lead Data
- `extract_section_content()` with per-type extractors for: hero, services, features, testimonials, contact, pricing, faq, navbar, footer, badges, cta
- `_fill_missing_content()` uses LLM (fast/Haiku tier) to generate industry-specific content for missing fields
- Never generates generic placeholder text -- always business/industry-specific

### Task 4: VLM Reference Decomposition
- `decompose_reference_site()` screenshots a URL and uses VLM to identify section boundaries
- Returns section type, y-position percentages, descriptions, and dominant colors
- Falls back through PlaywrightPool -> taste_analyzer screenshot chain

### Task 5: Build Tier System
- `DEMO_TIER`: $0/site, local Ollama inference, 4 sections max, 1 iteration
- `PREMIUM_TIER`: $0.19/site, Claude API, 8 sections max, 3 iterations
- `select_build_tier()` routes by site_type

### Task 6: Industry Section Templates
- 7 industry templates: dentist, plumber, restaurant, saas, legal, real_estate, default
- `get_section_template()` respects tier max_sections with smart truncation (keeps essential sections)
- Essential sections (hero, services/features, contact, footer) survive truncation

### Task 7: Tests (25 passing)
- Build plan generation, design contract validation, content extraction for all types
- Missing content LLM generation (mocked), industry templates, tier selection
- VLM decomposition (mocked), section priorities, full integration test

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Made site_builder import optional**
- **Found during:** Task 7 (tests)
- **Issue:** `from clawdbot.site_builder import DESIGN_DIRECTIONS` triggers shared.db -> psycopg_pool import chain, failing in test environments without Postgres
- **Fix:** Wrapped in try/except ImportError with fallback to `{"name": direction_name}` -- design tokens still resolve correctly from `_DIRECTION_TOKENS` presets
- **Files modified:** clawdbot/section_planner.py
- **Commit:** 906ff35

**2. [Rule 3 - Blocking] Copied Phase 33/34 dependency files into worktree**
- **Found during:** Task 1
- **Issue:** Worktree branch predates Phase 33/34 commits; design_tokens.py, renderer.py, etc. did not exist
- **Fix:** Copied 7 files from intel-integration branch (design_tokens, snippet_registry, renderer, visual_scorer, reference_manager, taste_profile, taste_analyzer)
- **Commit:** be71a30

## Known Stubs

None -- all functions are fully implemented with real logic and LLM fallback paths.

## Self-Check: PASSED

- clawdbot/section_planner.py: FOUND
- tests/test_phase35_section_planner.py: FOUND
- 35-1-SUMMARY.md: FOUND
- Commit be71a30: FOUND
- Commit 906ff35: FOUND
- 25/25 tests passing
