---
phase: "34"
plan: "rendering-vlm-infrastructure"
subsystem: clawdbot
tags: [rendering, vlm, playwright, visual-qa, scoring]
key-files:
  created:
    - clawdbot/renderer.py
    - clawdbot/visual_scorer.py
    - clawdbot/reference_manager.py
    - tests/test_phase34_renderer.py
    - tests/test_phase34_visual_scorer.py
    - tests/test_phase34_reference.py
  modified: []
decisions:
  - "Used asyncio.Semaphore + Queue for pool concurrency instead of lock-per-context"
  - "VLM JSON parser handles 3 formats: raw JSON, markdown-fenced, text-embedded"
  - "Design contract validator uses a static test HTML section (not generated)"
  - "Reference manager supports both yaml and plain-text metadata fallback"
metrics:
  tasks: 6/6
  tests: 40
  files-created: 6
  lines-added: ~1200
  completed: "2026-04-06"
---

# Phase 34: Rendering + VLM Infrastructure Summary

Playwright rendering service, VLM visual scoring pipeline, and reference manager -- the "eyes" for ClawdBot's visual production system.

## Commits

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 | Playwright rendering service + browser pool | e8ca351 | clawdbot/renderer.py |
| 2+3+4 | VLM scoring pipeline + backend abstraction + contract validator | c6fe074 | clawdbot/visual_scorer.py |
| 5 | Reference screenshot manager | 9647230 | clawdbot/reference_manager.py |
| 6 | Tests (40 passing) | (this commit) | tests/test_phase34_*.py |

## What Was Built

### Playwright Rendering Service (renderer.py)
- `render_html_to_png()` -- standalone HTML-to-PNG via headless Chromium
- `PlaywrightPool` -- pre-warmed browser pool with semaphore-guarded context reuse
- `render_multi_viewport()` -- desktop (1440x900), tablet (768x1024), mobile (375x812)
- `render_section_in_page()` -- wraps section HTML fragments in full page shell with design tokens CSS, Tailwind CDN, and Google Fonts
- `_build_page_shell()` -- generates complete HTML document with CSS custom properties from DesignTokens

### VLM Scoring Pipeline (visual_scorer.py)
- **Section quality** (`score_section_quality`): 6-dimension scoring (hierarchy, spacing, typography, color, component quality) with issue detection and actionable fixes
- **Reference comparison** (`compare_to_reference`): Match scoring across color, typography, and layout dimensions
- **Full-page rubric** (`score_full_page`): 10-point rubric with mandatory responsive + navigation gates
- **Design contract validator** (`validate_design_contract`): Pre-flight check renders test section with tokens, VLM-checks contrast/readability/aesthetics before section generation
- **Backend abstraction** (`_vlm_score`): Ollama (Qwen2.5-VL) primary, Claude Vision fallback, auto-failover
- **JSON parser** (`_parse_vlm_json`): Handles raw JSON, markdown-fenced JSON, and text-embedded JSON

### Reference Manager (reference_manager.py)
- `get_section_reference()` -- retrieve PNG by section_type + direction
- `capture_reference()` -- screenshot URL and store with metadata.yaml
- `list_references()` -- enumerate all references by section type
- `decompose_page_references()` -- VLM-identify sections in full-page screenshot
- Storage: `soul/references/{section_type}/{direction}.png`

## Feature Flags
- `VISUAL_QA_ENABLED=true` -- enables VLM-based visual quality scoring
- `VISUAL_QA_VLM_PROVIDER=ollama` -- "ollama" (free, default) or "claude" (paid)

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functions are fully implemented with proper VLM integration patterns.

## Test Coverage

40 tests across 3 test files:
- `test_phase34_renderer.py` (13 tests): render_html_to_png, PlaywrightPool lifecycle/render/multi-viewport, section-in-page rendering
- `test_phase34_visual_scorer.py` (18 tests): JSON parsing (7 formats), VLM backend (ollama/fallback/both-fail), section scoring, reference comparison, full-page rubric, design contract validation
- `test_phase34_reference.py` (9 tests): path construction, get/list/capture references, metadata management

## Self-Check: PASSED

- All 6 created files exist on disk
- All 3 task commits verified (e8ca351, c6fe074, 9647230)
- 40/40 tests passing
- ruff clean on all files
