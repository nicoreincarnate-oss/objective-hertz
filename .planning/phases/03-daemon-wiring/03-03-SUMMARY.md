---
phase: 03
plan: 03
wave: 3
title: Clawdbot Aider Loop Wiring (Layer B)
verdict: SHIP
status: complete
risk: medium-high
---

# Phase 3 Wave 3 — Clawdbot Aider Runtime Wiring — SUMMARY

**Verdict:** SHIP — opt-in Aider site-builder path wired into `clawdbot/site_builder.py` behind `ENABLE_AIDER_LOOPS=true` with full fall-through to the existing competitive 5-agent build.

## Commits

1. `43d004f` — feat(clawdbot): add aider_build handler for Phase 3 Wave 3 (opt-in)
2. `3e93b56` — feat(clawdbot): wire aider_build into site_builder with fall-through (feature-flagged)
3. `febafb3` — test(phase-3): add Clawdbot Aider build opt-in gate tests

## Files

- **Created:** `clawdbot/aider_build.py` (117 lines)
- **Created:** `tests/test_phase3_clawdbot_aider.py` (62 lines, 4 tests)
- **Modified:** `clawdbot/site_builder.py` — added import + opt-in branch in `_build_site` (entry for both `build_demo_site` and `build_full_site`)

## Validation

| Check | Result |
|-------|--------|
| `ast.parse(clawdbot/aider_build.py)` | OK |
| `from clawdbot.aider_build import ...` | OK |
| `ast.parse(clawdbot/site_builder.py)` | OK |
| `import clawdbot.site_builder` | OK |
| `pytest tests/test_phase3_clawdbot_aider.py -v` | 4 passed in 0.02s |
| Clawdbot regression isolation runs | Pre-existing infra failures only (DB pool, golden prompt LLM tests); not caused by this wave |

## Deviations from Plan

### [Rule 1 — Bug] Plan spec for `run_clawdbot_aider_loop` did not match canonical Phase 2 signature

- **Found during:** Task 1
- **Issue:** Plan code block called the loop with `brief=`, `brand=`, `sections_requested=`, `max_iterations=` and read `result.architect_model` / `result.editor_model` / `result.visual_score` directly. Canonical Phase 2 implementation in `shared/aider/clawdbot_loop.py` actually takes `lead_profile` (single positional dict), uses `max_iterations_per_section`, and stores model names on `result.page_plan.architect_model` / `result.sections[i].editor_model` and per-section visual scores.
- **Fix:** Adapted handler to translate caller-friendly `site_spec` (brief/brand/sections) into the loop's `lead_profile` shape via `_site_spec_to_lead_profile`, switched to the correct keyword args, and aggregated model names + average visual score from the result. Preserved the public handler API the plan asked for (`handle_aider_site_build(site_spec, *, llm_client, workspace) -> dict`).
- **Files:** `clawdbot/aider_build.py`
- **Commit:** `43d004f`

### [Rule 3 — Blocking] Wave 2 already added `ENABLE_AIDER_LOOPS` to `.env.example`

- **Found during:** Task 4
- **Issue:** Plan reserved Task 4 to append the env flag, but Wave 2 (Ruflo) shipped it first.
- **Fix:** Skipped Task 4 (no-op) per the prompt's explicit guidance.

## Auth Gates

None.

## Deferred Issues

Pre-existing test-collection errors (`test_phase19_cost_dashboard`, `test_phase24_dedup_broadcast`, `test_phase25_async_mcp`, `test_phase26b_daemon_migration`, `test_scout`, `test_self_audit`, `test_week3`, `test_week10`, `tests/titan/test_email_quality_gate`, `tests/tools/test_budget_policies`) — out of scope for this wave (unrelated import / module issues), logged here only because they appear in `pytest -k clawdbot` collection.

## Known Stubs

None. The handler is fully wired; only the deploy adapter (Aider HTML → Netlify) is intentionally deferred — `_build_site` currently logs the Aider success then falls through to the competitive deploy path so customers always receive a deployed URL. This is the documented design: turning the flag on yields telemetry today and full takeover once the deploy adapter lands in a future wave.

## Self-Check

```
FOUND: clawdbot/aider_build.py
FOUND: clawdbot/site_builder.py
FOUND: tests/test_phase3_clawdbot_aider.py
FOUND: 43d004f
FOUND: 3e93b56
FOUND: febafb3
```

## Self-Check: PASSED
