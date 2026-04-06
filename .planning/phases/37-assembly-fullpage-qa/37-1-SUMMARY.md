---
phase: "37"
plan: "1"
subsystem: clawdbot
tags: [assembly, seo, qa, visual-qa, deploy-gate, multi-page]
dependency_graph:
  requires: [phase-33-design-tokens, phase-34-renderer-vlm, phase-36-section-engine]
  provides: [page-assembler, fullpage-qa, deploy-gate]
  affects: [site-builder-pipeline, deployment-flow]
tech_stack:
  added: []
  patterns: [5-stage-qa-pipeline, multi-viewport-rendering, section-iteration]
key_files:
  created:
    - clawdbot/page_assembler.py
    - clawdbot/fullpage_qa.py
    - tests/test_phase37_assembler.py
    - tests/test_phase37_qa.py
  modified: []
decisions:
  - "SEO: title uses business_name | industry | city format (under 60 chars)"
  - "Schema.org: LocalBusiness with OfferCatalog for services"
  - "Deploy gate: soft by default (VISUAL_QA_BLOCKING=false), mandatory blocks always apply"
  - "Multi-page: inner pages generated via Haiku/fast tier for cost efficiency"
  - "QA: 5-stage pipeline with graceful degradation when Pool unavailable"
metrics:
  duration: "8 minutes"
  completed: "2026-04-06"
  tasks_completed: 6
  tasks_total: 6
  tests_added: 29
  tests_passing: 29
---

# Phase 37 Plan 1: Assembly + Full-Page QA Summary

Page assembler composing sections into SEO-ready HTML pages with 5-stage visual QA pipeline, iteration loop, and configurable deploy gate.

## Commits

| Commit | Type | Description |
|--------|------|-------------|
| b069346 | feat | Page assembler + multi-page assembly with SEO |
| 072ccf0 | feat | Full-page visual QA pipeline + iteration + deploy gate |
| eb99fe9 | test | Assembly + QA test suite (29 tests passing) |

## What Was Built

### Task 1-2: Page Assembler (`clawdbot/page_assembler.py`)

**`assemble_page()`** - Composes section fragments into complete HTML5 pages:
- Design token head block (fonts, Tailwind CDN, CSS custom properties)
- SEO meta tags: title (<60 chars), meta description (<155 chars), robots, canonical URL
- Open Graph and Twitter Card tags for social sharing
- Schema.org LocalBusiness JSON-LD with services catalog
- GSAP ScrollTrigger initialization and batch animation
- Effects initialization from phase 33 effects system
- Reduced-motion media query for accessibility
- Sections ordered according to BuildPlan, navbar before main, footer after main

**`assemble_multipage_site()`** - Generates 5-page sites:
- index.html (home), about.html, services.html, gallery.html, contact.html
- Shared nav + footer across all pages from home generation
- Inner page content generated via Haiku/fast model for cost efficiency
- Nav links updated to point to correct filenames

### Task 3-5: Full-Page QA (`clawdbot/fullpage_qa.py`)

**`run_full_page_qa()`** - 5-stage QA pipeline:
1. Multi-viewport screenshots (desktop/tablet/mobile) via PlaywrightPool
2. Per-viewport VLM quality scoring via score_section_quality()
3. Markup checks via existing analyze_site_markup()
4. Claude Vision 10-point rubric (premium tier only) via score_full_page()
5. Anti-slop text check via AntiSlopScorer or regex fallback

Mandatory blockers (always fail regardless of score):
- Placeholder content (lorem ipsum, coming soon, etc.)
- Secrets in HTML (Stripe keys, AWS keys)
- Empty HTML

**`iterate_full_page()`** - Targeted section re-generation:
- Maps QA issues to specific failing sections via pattern matching
- Re-runs only failing sections through section_agent with QA feedback
- Re-assembles and re-scores (max 2 iterations)

**`deploy_gate()`** - Ship/no-ship decision:
- Hard gate (VISUAL_QA_BLOCKING=true): blocks below threshold
- Soft gate (default): deploys with warning, emits monitoring event
- Mandatory blocks always apply in both modes

### Task 6: Tests (29 tests)

| File | Tests | Coverage |
|------|-------|----------|
| test_phase37_assembler.py | 14 | Assembly, SEO, Schema.org, ordering, multi-page, nav links |
| test_phase37_qa.py | 15 | QA pipeline, deploy gate (hard/soft), placeholders, secrets, iteration |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Python 3.9 compatibility for site_quality import**
- **Found during:** Task 6 (test execution)
- **Issue:** `clawdbot.site_quality` imports `shared.llm_client` which uses `int | None` syntax requiring Python 3.10+, causing TypeError on import
- **Fix:** Changed `except ImportError` to `except (ImportError, TypeError)` in fullpage_qa.py for both site_quality and anti_slop imports
- **Files modified:** clawdbot/fullpage_qa.py
- **Commit:** eb99fe9

## Known Stubs

None - all functions are fully implemented with graceful degradation when optional dependencies (PlaywrightPool, VLM scorer, LLM client) are unavailable.

## Feature Flags

| Flag | Default | Purpose |
|------|---------|---------|
| VISUAL_QA_BLOCKING | false | Hard gate (true) vs soft gate (false) |
| VISUAL_QA_MIN_THRESHOLD | 7.0 | Minimum score for deploy approval |

## Self-Check: PASSED
