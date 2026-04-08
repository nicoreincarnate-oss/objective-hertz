---
phase: 03
plan: 04
title: Hermes Voice Loop Wiring (Layer C — dead-path)
wave: 4
status: complete
verdict: SHIP
duration_minutes: 12
commits:
  - 5d2d3a1 feat(hermes): add voice_session for Phase 3 Wave 4 (Parakeet+Kokoro+intent_router)
  - 6b62a7a test(phase-3): add Hermes voice loop opt-in gate tests
files_created:
  - hermes/jarvis/__init__.py
  - hermes/jarvis/voice_session.py
  - tests/test_phase3_hermes_voice.py
files_modified:
  - .env.example
---

# Phase 3 Plan 04 — Hermes Voice Loop Wiring Summary

## Verdict: SHIP

Layer C voice loop wired into Hermes. Module imports cleanly on a machine
with **no Parakeet/Kokoro daemons running** — confirmed by direct import
and the dedicated `test_voice_session_imports_cleanly_without_services`
test. The path stays DEAD by design until the operator runs the Phase
42.5 v2 install checklist for the Mac Studio voice services.

## What was built

**`hermes/jarvis/voice_session.py`** orchestrates the full voice loop:

1. `voice_loop_enabled()` reads `ENABLE_VOICE_LOOP` env flag (default false).
2. `handle_voice_input(audio_bytes, *, llm_client)` runs:
   - Lazy-imports `ParakeetClient`, `KokoroClient`, `route_voice_intent`
   - Runs `health_check()` on both daemons → status=skipped if down
   - Transcribes via Parakeet → empty transcript → status=failed
   - Routes intent via `route_voice_intent()` (returns `VoiceIntent`)
   - Synthesizes spoken response via Kokoro
   - Returns dict with status complete/skipped/failed plus payload

**Adaptation to real API surface** (not the plan's placeholders):
- `health_check()` instead of `is_available()` (real method on both clients)
- `route_voice_intent()` instead of `route_intent()` (top-level helper)
- `VoiceIntent.natural_response` / `.daemon` attribute access (dataclass)
- `TranscriptionResult.text` / `SpeechResult.audio_bytes` attribute access

**LAZY IMPORT contract**: every `from shared.voice.*` import lives inside
`handle_voice_input()`, wrapped in try/except `ImportError` → status=skipped.
Module-top imports are stdlib only (`logging`, `os`, `typing`).

## Validation

| Check | Result |
|---|---|
| AST parse new files | OK |
| `from hermes.jarvis.voice_session import ...` (no daemons running) | **OK** |
| `pytest tests/test_phase3_hermes_voice.py -v` | **6 passed** |
| Pre-existing hermes tests still pass | 9 passed (8 unrelated failures pre-date this plan) |

```
tests/test_phase3_hermes_voice.py::test_voice_session_imports_cleanly_without_services PASSED
tests/test_phase3_hermes_voice.py::test_voice_loop_disabled_by_default PASSED
tests/test_phase3_hermes_voice.py::test_voice_loop_enabled_when_flag_true PASSED
tests/test_phase3_hermes_voice.py::test_handler_skipped_when_disabled PASSED
tests/test_phase3_hermes_voice.py::test_handler_skipped_when_services_unreachable PASSED
tests/test_phase3_hermes_voice.py::test_handler_skipped_when_health_check_raises PASSED
```

## Deviations from plan

- **[Rule 1 — Bug] Plan referenced non-existent client methods.** Plan
  example used `is_available()` and `route_intent()`. Real APIs are
  `health_check()` (both clients) and `route_voice_intent()` (intent_router).
  Adapted handler to actual surface area. No `shared/voice/*` files modified.
- **[Task 2 — Deferred] `hermes/jarvis/vision_loop.py` does not exist.**
  Task 2 said "do not change vision loop's default behavior" — there is no
  vision loop yet. No file created (would be scope creep into Wave 5+).
  The minimal opt-in path is already exposed via the `hermes.jarvis`
  package: `from hermes.jarvis.voice_session import handle_voice_input`.
  Logged for a future plan that introduces vision_loop.py.
- **[Bonus] Added 6th test** `test_handler_skipped_when_health_check_raises`
  to lock the contract that network/timeout exceptions during liveness
  checks degrade gracefully to skipped, never propagate.

## Pre-existing issues observed (out of scope, not fixed)

- `tests/test_hermes_dashboard.py`, `test_hermes_health_api.py`,
  `test_hermes_insights.py`: 8 failures from `ModuleNotFoundError:
  hermes.web.jarvis_ws`. Predates this plan, unrelated to voice_session.

## Confirmation

> **The `hermes.jarvis.voice_session` module imports cleanly without
> Parakeet or Kokoro services running.** Verified twice:
> (a) `python3 -c "from hermes.jarvis.voice_session import ..."` → OK
> (b) `test_voice_session_imports_cleanly_without_services` → PASSED

Layer C dead path is in place. Ready for operator to flip
`ENABLE_VOICE_LOOP=true` once Parakeet (`127.0.0.1:11440`) and Kokoro
(`127.0.0.1:11441`) daemons are installed via Phase 42.5 v2 checklist.

## Self-Check: PASSED

- hermes/jarvis/voice_session.py — FOUND
- hermes/jarvis/__init__.py — FOUND
- tests/test_phase3_hermes_voice.py — FOUND
- .env.example contains ENABLE_VOICE_LOOP — FOUND
- Commit 5d2d3a1 — FOUND
- Commit 6b62a7a — FOUND
