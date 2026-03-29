---
phase: 01-agent-dna
plan: 02
subsystem: llm
tags: [dna, llm-client, feature-flag, circuit-breaker, daemon-integration]

# Dependency graph
requires:
  - phase: 01-agent-dna plan 01
    provides: AgentDNA loader, DNACircuitBreaker, per-daemon YAML profiles, engineering_dna.md
provides:
  - DNA injection in LLMClient.generate() via use_dna/daemon_name parameters
  - All daemon reasoning calls wired to pass DNA
  - 12 new integration tests for injection, boundaries, scanner, zero-change
affects: [anti-slop, middleware, pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns: [opt-in DNA injection via generate() params, feature-flag gated behavior]

key-files:
  created: []
  modified:
    - shared/llm_client.py
    - titan/pipeline/email_compose.py
    - titan/pipeline/close_deal.py
    - titan/expansion.py
    - perseus/sleep_cycle.py
    - perseus/self_audit.py
    - hermes/daemon.py
    - hermes/web/insights.py
    - clawdbot/brain.py
    - tests/shared/test_agent_dna.py

key-decisions:
  - "Conway has no LLM calls -- no DNA injection needed for Conway daemon"
  - "Only main reasoning calls get DNA, not parsing/extraction calls (plan-specified)"
  - "_inject_dna is static method for testability without network"

patterns-established:
  - "Opt-in DNA: use_dna=True, daemon_name='X' on generate() calls that need personality"
  - "Feature flag ENABLE_DNA_PROFILES gates all DNA behavior -- zero change when off"

requirements-completed: [DNA-05]

# Metrics
duration: 5min
completed: 2026-03-29
---

# Phase 1 Plan 02: DNA Injection + Daemon Integration Summary

**DNA injected into LLM generate() with use_dna/daemon_name params, 9 daemon reasoning calls wired, 36 tests passing**

## Performance

- **Duration:** 5 min
- **Started:** 2026-03-29T21:05:27Z
- **Completed:** 2026-03-29T21:10:26Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments
- Added `use_dna` and `daemon_name` parameters to `LLMClient.generate()` with `_inject_dna()` static method
- Wired 9 main reasoning LLM calls across 4 daemons (Titan: 3, Perseus: 3, Hermes: 2, ClawdBot: 1)
- Added 12 new integration tests covering injection paths, behavioral boundaries, mocked scanner, and zero-change guarantees
- All 36 tests pass (24 existing from Wave 1 + 12 new)

## Task Commits

Each task was committed atomically:

1. **Task 1: Inject DNA into generate() + daemon integration** - `04a5baf` (feat)
2. **Task 2: Test suite -- injection, boundaries, scanner, zero-change** - `cb4d89b` (test)

## Files Created/Modified
- `shared/llm_client.py` - Added use_dna/daemon_name params to generate(), _inject_dna() static method
- `titan/pipeline/email_compose.py` - Added use_dna=True, daemon_name="titan" to email generation call
- `titan/pipeline/close_deal.py` - Added use_dna=True, daemon_name="titan" to deal reasoning call
- `titan/expansion.py` - Added use_dna=True, daemon_name="titan" to expansion decision call
- `perseus/sleep_cycle.py` - Added use_dna=True, daemon_name="perseus" to alpha and beta analysis calls
- `perseus/self_audit.py` - Added use_dna=True, daemon_name="perseus" to audit reasoning call
- `hermes/daemon.py` - Added use_dna=True, daemon_name="hermes" to routing decision call
- `hermes/web/insights.py` - Added use_dna=True, daemon_name="hermes" to insight generation call
- `clawdbot/brain.py` - Added use_dna=True, daemon_name="clawdbot" to skill routing call
- `tests/shared/test_agent_dna.py` - Added 12 new tests for injection, boundaries, scanner, zero-change

## Decisions Made
- Conway wallet.py has zero LLM calls, so no DNA injection was needed for the Conway daemon
- Only updated "main reasoning" LLM calls per the plan -- simple extraction/parsing calls (e.g., email extraction in clawdbot/daemon.py) left untouched
- Made `_inject_dna()` a static method for easy unit testing without requiring network or async context

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Injection scanner mock tests initially failed because `InjectionScanner` is a local import inside `_scan_for_injection()` -- fixed by patching `sys.modules` instead of the module attribute.

## User Setup Required
None - no external service configuration required. Set `ENABLE_DNA_PROFILES=true` env var to activate DNA injection.

## Next Phase Readiness
- Phase 1 (Agent DNA) is now complete -- all DNA-* requirements delivered
- DNA profiles load and inject into all daemon reasoning calls when feature flag is on
- Ready for Phase 2 (Anti-Slop Quality Gate) which will use DNA-aware LLM calls

---
*Phase: 01-agent-dna*
*Completed: 2026-03-29*
