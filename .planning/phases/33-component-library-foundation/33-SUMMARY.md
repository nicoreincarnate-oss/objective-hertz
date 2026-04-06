---
phase: "33"
plan: "component-library-foundation"
subsystem: clawdbot
tags: [design-tokens, component-library, effects, are.na, asset-generation]
dependency_graph:
  requires: []
  provides: [design-tokens, snippet-registry, effects-layer, arena-client, asset-pipeline]
  affects: [clawdbot/site_builder.py, clawdbot/taste_profile.py]
tech_stack:
  added: [GSAP 3.12.5, Motion v12, p5.js v2, Alpine.js 3.14, Tailwind v4 CDN, Lenis, Rive, Three.js, Spline, Lottie, Tone.js]
  patterns: [design-tokens-to-css-vars, yaml-frontmatter-snippets, effect-injection, prefers-reduced-motion]
key_files:
  created:
    - clawdbot/design_tokens.py
    - clawdbot/snippet_registry.py
    - clawdbot/effects.py
    - clawdbot/arena_client.py
    - clawdbot/asset_generator.py
    - soul/components/ (30 HTML snippets across 9 section types)
    - tests/test_phase33_design_tokens.py
    - tests/test_phase33_snippets.py
    - tests/test_phase33_effects.py
    - tests/test_phase33_arena.py
    - tests/test_phase33_assets.py
  modified: []
decisions:
  - Used pre-defined token mappings for all 11 directions instead of LLM-parsed prose
  - CSS custom properties as the universal theming bridge between tokens and snippets
  - YAML frontmatter for snippet metadata enables programmatic selection
  - All effects wrapped in prefers-reduced-motion checks for accessibility
  - Are.na public API needs no auth for read operations
metrics:
  duration_seconds: 939
  completed: "2026-04-06"
  tasks_completed: 8
  tasks_total: 8
  tests_total: 35
  tests_passing: 35
  files_created: 41
  files_modified: 0
---

# Phase 33: Component Library Foundation Summary

DesignTokens system with 11-direction token mappings, 30 HTML snippets across 9 section types, 15 JS/CSS effects, Are.na API client, and asset generation pipeline with Recraft/Flux/Kling/p5 support.

## What Was Built

### 1. DesignTokens Data Model + CDN Stack (`clawdbot/design_tokens.py`)
- `DesignTokens` dataclass covering colors (8 slots), typography (10 fields), spacing (5), visual (4), motion (3), layout (1), and direction metadata
- Pre-defined exact token mappings for all 11 design directions (5 original from site_builder + 6 taste-derived from taste_profile)
- `CDN_STACK` with 13 pinned CDN URLs (Tailwind v4, Alpine 3.14, GSAP 3.12.5, ScrollTrigger, Motion v12, Lenis, Lottie, Rive, Three.js, Spline, p5.js v2, Tone.js, PixiJS)
- `DIRECTION_CDN_MAP` mapping each of 11 directions to required CDN dependencies
- `extract_tokens_from_direction()` with color/font prose parsing fallback
- `extract_tokens_from_taste()` merges VLM-extracted palette colors over direction defaults
- `tokens_to_css_vars()` generates `:root` block with 27 custom properties
- `tokens_to_prompt_block()` for LLM prompt injection
- `tokens_to_tailwind_config()` generates Tailwind v4 `@theme` block
- `build_head_block()` generates complete `<head>` with meta, fonts, CDN deps, CSS vars, reduced-motion query

### 2. Vanilla Component Snippet Registry (`soul/components/`)
30 production-quality HTML snippets across 9 section types:
- **hero/** (6): split-image-cta, centered-gradient-bg, aurora-dark-canvas, editorial-oversized-type, minimal-geometric, video-background
- **features/** (6): bento-grid-icons, three-column-cards, alternating-image-text, stats-counter-bar, icon-list-compact, services-grid
- **testimonials/** (4): carousel-cards, grid-with-stars, large-quote-spotlight, logo-bar
- **pricing/** (3): three-tier-cards, toggle-monthly-annual, single-card-highlight
- **cta/** (3): dark-gradient-centered, split-form-benefits, newsletter-minimal
- **contact/** (2): form-with-info, map-with-form
- **faq/** (2): accordion-alpine, two-column-qa
- **navbar/** (2): sticky-blur-mobile, transparent-scroll-reveal
- **footer/** (2): four-column-dark, minimal-centered

All snippets use CSS custom properties (no hardcoded colors), Tailwind utilities, Alpine.js for interactivity, and `{{placeholder}}` template vars.

### 3. Snippet Loader + Selector (`clawdbot/snippet_registry.py`)
- `load_snippet()` reads HTML files with YAML frontmatter parsing
- `list_available_snippets()` scans directory tree
- `select_snippets_for_build()` picks best variant per section based on direction match + taste weights + complexity
- `validate_snippet()` checks CSS var usage and root element presence

### 4. Effects Translation Layer (`clawdbot/effects.py`)
15 premium effects translated from Aceternity/Magic UI/Awwwards patterns:
- GSAP (5): fade_up_scroll, stagger_children, parallax_bg, split_text_reveal, pin_scrub
- Motion vanilla JS (3): spring_hover, layout_animate, exit_animate
- Vanilla JS/CSS (5): number_counter, text_shimmer, spotlight_cursor, magnetic_button, marquee_ticker
- Interactive (1): card_tilt_3d
- Generative (1): p5_particles
- All respect `prefers-reduced-motion`

### 5. Are.na Design Reference Client (`clawdbot/arena_client.py`)
- Async httpx client for Are.na public REST API
- `search_channels()`, `get_channel_blocks()`, `search_blocks()`, `get_image_urls()`
- No auth required for public endpoints

### 6. Asset Generation Pipeline (`clawdbot/asset_generator.py`)
- `generate_hero_assets()` orchestrates per-tier generation (demo vs premium)
- `generate_svg_icon()` via Recraft V4 API
- `generate_hero_image()` via Flux 2 Pro / fal.ai
- `generate_video_background()` via Kling AI 3.0
- `generate_generative_background()` returns p5.js sketches (particles + gradient mesh)

## Decisions Made

1. **Pre-defined token mappings over LLM parsing**: Each of the 11 directions has exact hex/font/spacing values defined in `_DIRECTION_TOKENS` rather than relying on runtime LLM parsing of prose descriptions. This eliminates non-determinism in token extraction.

2. **CSS custom properties as universal bridge**: All snippets use `var(--color-primary)`, `var(--font-display)` etc., making them direction-agnostic. The tokens generate the `:root` block that wires everything together.

3. **YAML frontmatter for selection metadata**: Each snippet declares `style_direction`, `complexity`, and `cdn_deps` in frontmatter, enabling programmatic variant selection without parsing HTML.

4. **Reduced-motion by default**: Every effect checks `prefers-reduced-motion` before executing. This is mandatory accessibility, not optional.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all components are fully functional. API integrations (Recraft, Flux, Kling) gracefully degrade when keys are missing, which is correct production behavior, not a stub.

## Self-Check: PASSED

- All 5 Python modules exist in clawdbot/
- 30 HTML snippets in soul/components/
- All 5 test files exist
- All 7 commits verified (c25bf12, 081c68a, 42488f2, 74cd5f1, 38669bc, 74816e0, 6a7aa96)
- 35/35 tests passing
