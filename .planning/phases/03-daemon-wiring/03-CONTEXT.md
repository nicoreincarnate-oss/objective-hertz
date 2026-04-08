# Phase 3: Daemon Wiring - Context

**Gathered:** 2026-04-08 (autonomous run, operator "go all")
**Mode:** Auto-generated (workflow.skip_discuss=true)
**Source of truth:** PORT-PLAN.md + Phase 42.5 v2 spec + operator benchmark-first rule (2026-04-08)

## Phase Boundary

Wire the new Phase 42.5 v2 modules into all 8 daemons so they actually run. Phase 1+2 built the modules and rebased to main. Phase 3 makes them live.

**151 call sites** surveyed across 82 files (migrator fix landed in pre-flight):
- openjarvis: 45
- shared: 41 (internal utilities)
- titan: 29
- clawdbot: 15
- hermes: 6
- perseus: 6
- ruflo: 4
- scripts: 3
- orchestrator.py: 1
- conway: 1 (false positive — Conway has no LLM calls per prior audit)

## Non-negotiable preservations

All of these MUST survive Phase 3:
- **P0-5** scrypt KDF in `shared/escalation_log/redactor.py`
- **P0-6** fail-closed crypto imports
- **P0-9** self-consistency `attempted_samples`/`successful_samples` fields
- **P0-10** grammar compiler `UnsupportedSchemaFeatureError`
- **P1-7** LeadWorkerLoop deepcopy at all 5 callback sites
- **main's 1711-line llm_client.py** (do NOT revert tier routing, fallback chain, StickyLatch, watchdog, AirLLM)
- **Trinity swap** in `shared/tiers.py` + `config/litellm_config.yaml` (commit 7212029)
- **All 41 phase 23 tests** passing

## Feature flag strategy

All wiring lands behind OFF-by-default flags so daemons don't change behavior until operator flips them:

- `AUTO_TIER_ENABLED=false` — auto-classifier routing (opt-in per call site via `auto_tier=True` kwarg too)
- `ENABLE_AIDER_LOOPS=false` — Ruflo + Clawdbot Aider architect+editor paths
- `ENABLE_VOICE_LOOP=false` — Hermes Parakeet+Kokoro+intent_router
- `ENABLE_DRAW_THINGS=false` — Clawdbot local image gen fallback

## Wave breakdown

### Wave 1 — 03-01 Metadata plumbing (Layer A, lowest risk)
Run `migrate_to_litellm.py --apply` across 8 daemons. Each call site gets `daemon_name=` + `operation=` kwargs inserted. Multi-line calls get deferred to `/tmp/migrate_to_litellm_manual_review.json`. Atomic commit per daemon (8 commits).

Also: wire `shared.spend_alerts` import into `shared/middleware.py:check_budget_for_llm_call` so budget threshold alerts fire via the existing Hermes pipeline.

### Wave 2 — 03-02 Ruflo Aider wiring (Layer B)
Add a new `ruflo/aider_handler.py` that accepts bug-fix tasks and routes them through `shared.aider.ruflo_loop.run_ruflo_aider_loop`. Update `ruflo/agent.py:dispatch` to call the Aider handler when `ENABLE_AIDER_LOOPS=true` (keep existing path when false). SandboxRunner is the default for the verifier sub-step (P0-3).

### Wave 3 — 03-03 Clawdbot Aider wiring (Layer B)
Add a new optional entry point `clawdbot/aider_build.py` that wraps `shared.aider.clawdbot_loop.run_clawdbot_aider_loop`. Hook into `clawdbot/site_builder.py` behind `ENABLE_AIDER_LOOPS=true`. Keep the existing competitive 5-agent build as the default path; Aider is opt-in for the "pure local" build path.

### Wave 4 — 03-04 Hermes voice loop (Layer C, dead path until services alive)
Add `hermes/jarvis/voice_session.py` that uses `shared.voice.parakeet_client`, `shared.voice.kokoro_client`, and `shared.voice.intent_router`. Wire into `hermes/jarvis/vision_loop.py` as an alternate entry point behind `ENABLE_VOICE_LOOP=true`. This creates a dead code path until operator installs Parakeet + Kokoro on the Studio (Phase 42.5 v2 operator checklist item).

### Wave 5 — 03-05 Clawdbot image gen (Layer C, dead path until Draw Things alive)
Add `clawdbot/asset_generator.py` draw_things branch that invokes `shared.imagegen.draw_things_client` when `ENABLE_DRAW_THINGS=true` AND Draw Things HTTP API is reachable at `127.0.0.1:7860`. Fall back to Recraft (the existing production path per CARL decision 2026-03-30). Dead path until operator installs Draw Things.

## Out of scope

- Starting daemons (operator launch task)
- Installing brew/pip/ollama packages (operator Studio task)
- Editing `.env` (operator secret-management task)
- Modifying `shared/llm_client.py` (canonical main version, do not touch)
- Setting `OPENROUTER_API_KEY` (operator secret-management)
