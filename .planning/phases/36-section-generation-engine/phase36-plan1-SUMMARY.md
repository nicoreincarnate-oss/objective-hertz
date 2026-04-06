---
phase: "36"
plan: "1"
subsystem: clawdbot
tags: [section-generation, vlm-feedback, parallel-orchestration, site-builder-v2]
dependency_graph:
  requires: [phase33-design-tokens, phase34-renderer-scorer, phase35-section-planner]
  provides: [section-agent, section-orchestrator, iteration-strategy]
  affects: [clawdbot/site_builder.py, phase37-assembly]
tech_stack:
  added: []
  patterns: [asyncio-semaphore-throttling, vlm-feedback-loop, patch-vs-regenerate-iteration]
key_files:
  created:
    - clawdbot/section_agent.py
    - clawdbot/section_orchestrator.py
    - tests/test_phase36_section_agent.py
    - tests/test_phase36_orchestrator.py
  modified: []
decisions:
  - "Patch iterations (rounds 1-2) preserve working HTML; regenerate (round 3) starts fresh with accumulated anti-patterns"
  - "Semaphore(5) for LLM concurrency, Semaphore(2) for VLM — matches M4 32GB VRAM limits"
  - "Pool is optional (None skips rendering/scoring) so generation works without Playwright"
  - "Cost estimation uses blended input+output rates per model tier"
metrics:
  duration_s: 1150
  completed: "2026-04-06"
  tasks_completed: 6
  tasks_total: 6
  tests_added: 31
  files_created: 4
  files_modified: 0
---

# Phase 36 Plan 1: Section Generation Engine Summary

**Section generation engine with VLM visual feedback loop, patch/regenerate iteration, and parallel orchestration across semaphore-throttled asyncio tasks.**

## What Was Built

### Section Agent (`clawdbot/section_agent.py`)
- `SectionResult` dataclass with cost/time/token tracking fields
- `generate_section()` -- full VLM feedback loop: generate -> render -> score -> iterate
- `build_section_prompt()` -- assembles design contract, tokens as CSS vars, business content, snippet example, anti-slop rules into structured LLM prompt
- `extract_section_html()` -- strips markdown fences, finds `<section>` tags, wraps bare HTML
- `validate_section_html()` -- catches placeholder URLs, Lorem ipsum, missing tags, length issues
- `_iterate_section()` -- rounds 1-2 PATCH (fix specific VLM issues), round 3 REGENERATE (fresh with accumulated anti-patterns)
- `_call_llm()` -- routes "smart"/"fast"/"genius" to shared.llm_client, "ollama:*" to direct HTTP
- `_estimate_token_cost()` -- per-model blended rate estimation

### Parallel Orchestrator (`clawdbot/section_orchestrator.py`)
- `BuildResult` aggregates all section results with total cost/time/iterations
- `build_all_sections()` -- runs all sections via `asyncio.gather` with semaphore throttling
- `SectionCost` dataclass for per-section cost breakdown (tokens, cost, VLM calls, time)
- `estimate_section_cost()` -- model cost map (fast=$0.001, smart=$0.015, genius=$0.075, ollama=$0)
- `_emit_progress()` -- emits DB events with safe fallback when DB unavailable
- `log_build_costs()` -- persists cost breakdown to `site_build_metrics` table
- Failed sections are collected but do not block other sections

### Test Suite
- `tests/test_phase36_section_agent.py` -- 21 tests: extraction (6), validation (6), prompt building (4), generation (2), iteration strategy (2), VLM feedback (1)
- `tests/test_phase36_orchestrator.py` -- 10 tests: parallel execution (4), cost tracking (5), progress events (1)

## Decisions Made

1. **Patch vs Regenerate**: Rounds 1-2 send current HTML + VLM issues for targeted fixes (cheaper, faster). Round 3 regenerates from scratch with accumulated issues as anti-patterns (clean slate when patching fails).
2. **Concurrency model**: Semaphore(5) for LLM calls, Semaphore(2) for VLM/Ollama calls. Matches M4 32GB hardware limits while maximizing pipeline overlap.
3. **Optional pool**: When `pool=None`, generation skips rendering/scoring and returns on first successful validation. This makes the agent usable in test environments and CLI tools.
4. **Python 3.9 compat**: Used `noqa: B905` for zip() without strict param, sys.modules injection for mock modules in tests.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all functions are fully implemented with real logic.

## Self-Check: PASSED

All 4 created files exist. All 3 task commits verified (beb5677, 4f0883a, f09e496). 31/31 tests passing.
