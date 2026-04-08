---
phase: 03
plan: 05
wave: 5
title: Clawdbot Draw Things Image Gen Wiring (Layer C — dead-path)
verdict: SHIP
status: complete
date: 2026-04-08
commits:
  - 90f7126 feat(clawdbot): wire Draw Things fallback branch into asset_generator (Phase 3 Wave 5)
  - e3399fa test(phase-3): add Clawdbot Draw Things opt-in gate tests
---

# Phase 3 Plan 05 — Summary

**Verdict: SHIP**

Wave 5 (final Phase 3 wave) wires `shared.imagegen.draw_things_client.DrawThingsClient` into `clawdbot/asset_generator.generate_hero_image` as an opt-in Layer C dead-path. Recraft/fal.ai remain the production default per CARL decision 2026-03-30. The branch only activates when the operator both installs Draw Things at 127.0.0.1:7860 AND sets `ENABLE_DRAW_THINGS=true`.

## Task-by-task

| # | Task | Status |
|---|------|--------|
| 1 | Investigate Draw Things client API | DONE — read shared/imagegen/draw_things_client.py in full |
| 2 | Investigate clawdbot/asset_generator.py | DONE — identified `generate_hero_image()` as the dispatch entry point (line 153) |
| 3 | Wire Draw Things branch | DONE — injected at top of `generate_hero_image`, falls through to existing fal.ai path on any failure |
| 4 | Feature flag in `.env.example` | DONE — appended `ENABLE_DRAW_THINGS=false` with Phase 3 Wave 5 comment |
| 5 | Tests | DONE — 4 tests, all green |

## Plan placeholders vs actual API

The plan used placeholder method names. Real API discovered in `shared/imagegen/draw_things_client.py`:

| Plan placeholder | Actual API |
|---|---|
| `dt.is_available()` | `dt.health_check()` (async, GETs `/sdapi/v1/options`) |
| `dt.generate(prompt=, style=, size=)` | `dt.generate(prompt, *, preset='hero_fast', negative_prompt=, seed=, custom_params=)` |
| `result.success` | No success field — `generate()` raises `RuntimeError` on no images, otherwise returns `ImageGenResult` |
| `result.image_bytes` | `result.image_bytes` (bytes field) — confirmed correct |

Mapping applied: `style="editorial"` → preset `hero_quality` (Flux.1 dev 25-step), all other styles → preset `hero_fast` (SDXL 25-step).

## Validation results

| Check | Result |
|---|---|
| `python3 -c "import ast; ast.parse(...)"` | OK |
| `python3 -c "import clawdbot.asset_generator"` | IMPORT OK |
| `python3 -c "from shared.imagegen.draw_things_client import DrawThingsClient"` | DT IMPORT OK |
| `pytest tests/test_phase3_clawdbot_draw_things.py -v` | 4 passed in 0.02s |
| `grep -c 'ENABLE_DRAW_THINGS' clawdbot/asset_generator.py` | 2 (>= 1, gate confirmed) |
| Targeted clawdbot regression (`test_clawdbot_activation`, `test_clawdbot_runtime_capabilities`, `evals/test_eval_clawdbot`, new file) | 26 passed in 0.30s — zero regressions |

Note: Repo-wide `pytest -k clawdbot` collection has 10 pre-existing collection errors in unrelated test files (test_phase19_cost_dashboard, test_phase24_dedup_broadcast, test_phase25_async_mcp, test_phase26b_daemon_migration, test_scout, test_self_audit, test_week3, test_week10, titan/test_email_quality_gate, tools/test_budget_policies). These pre-date this wave and are out of scope (Rule: scope boundary). Logged here for awareness.

## Preservation confirmation

- `clawdbot/asset_generator.py` Recraft/fal.ai code path: **untouched** (line 200+ in original ordering, now after the new opt-in branch)
- CARL decision 2026-03-30 honored: Recraft is the production default
- `shared/imagegen/draw_things_client.py`: **not modified** (canonical from Phase 2)
- `.env`: not touched, only `.env.example` appended
- With `ENABLE_DRAW_THINGS=false` (default), the branch is skipped entirely — confirmed by `test_draw_things_disabled_by_default` (asserts `DrawThingsClient` is never instantiated)

## Files changed

- `clawdbot/asset_generator.py` — added 44 lines (opt-in branch + comments) at top of `generate_hero_image`
- `.env.example` — added 3 lines (header comment + `ENABLE_DRAW_THINGS=false`)
- `tests/test_phase3_clawdbot_draw_things.py` — new file, 97 lines, 4 tests

## Commits

- `90f7126` feat(clawdbot): wire Draw Things fallback branch into asset_generator (Phase 3 Wave 5)
- `e3399fa` test(phase-3): add Clawdbot Draw Things opt-in gate tests

## Blockers

None.

## Self-Check: PASSED

- clawdbot/asset_generator.py: FOUND, modified, AST + import clean
- .env.example: FOUND, contains `ENABLE_DRAW_THINGS=false`
- tests/test_phase3_clawdbot_draw_things.py: FOUND, 4 tests pass
- Commit 90f7126: present in git log
- Commit e3399fa: present in git log
