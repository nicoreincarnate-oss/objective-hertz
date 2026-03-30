---
phase: 10-21st-dev-recraft-pipeline-integration
plan: 01
subsystem: site-builder
tags: [21st-dev, component-api, design-sources, httpx, rest-client]

requires:
  - phase: 08-tribe-v2-neuro-scorer
    provides: pipeline integration patterns and middleware chain
provides:
  - tools/twentyfirst_client.py REST API client for 21st.dev component fetching
  - clawdbot/design_sources.py async enrichment with real component code
  - Graceful degradation when TWENTYFIRST_API_KEY is missing
affects: [10-02, site-builder-pipeline, clawdbot-variant-prompts]

tech-stack:
  added: [21st.dev Magic API via httpx]
  patterns: [conditional-import-for-graceful-degradation, async-enrichment-wrapper, thin-mockable-wrapper]

key-files:
  created: [tools/twentyfirst_client.py, tests/test_twentyfirst_client.py]
  modified: [clawdbot/design_sources.py, tests/test_design_sources.py]

key-decisions:
  - "Direct REST calls to magic.21st.dev instead of MCP protocol (simpler, mirrors recraft_client pattern)"
  - "Max 2 component fetches per curated source to stay within rate limits"
  - "Component code framed as structural inspiration with React/TSX adaptation rule"
  - "_fetch_component thin wrapper enables clean test mocking without httpx internals"

patterns-established:
  - "Conditional import inside async function for optional external dependencies"
  - "Thin _fetch_component wrapper pattern for testability of async API calls"

requirements-completed: [TWENTY1-01, TWENTY1-02, TWENTY1-03]

duration: 2min
completed: 2026-03-30
---

# Phase 10 Plan 01: 21st.dev + Design Sources Enrichment Summary

**21st.dev REST API client fetching real component code, wired into design_sources.py as structural inspiration for site builder variants**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-30T22:13:26Z
- **Completed:** 2026-03-30T22:16:16Z
- **Tasks:** 2 (both TDD: RED-GREEN)
- **Files modified:** 4

## Accomplishments
- Created `tools/twentyfirst_client.py` -- REST client calling POST /api/fetch-ui on magic.21st.dev with x-api-key auth
- Added `resolve_design_sources_with_components()` to design_sources.py -- async function that enriches 21st.dev curated sources with real component code snippets
- All 15 tests passing (7 client + 8 design sources) with full graceful degradation coverage

## Task Commits

Each task was committed atomically (TDD: test then feat):

1. **Task 1: Create 21st.dev REST API client and tests**
   - `869941d` (test) -- failing tests for client
   - `935b8ec` (feat) -- implement client, all 7 tests pass
2. **Task 2: Wire component enrichment into design_sources.py and extend tests**
   - `a68e0c9` (test) -- failing tests for enrichment
   - `89d2529` (feat) -- implement enrichment, all 15 tests pass

## Files Created/Modified
- `tools/twentyfirst_client.py` -- 21st.dev REST API client (94 lines): fetch_component_inspiration(), get_twentyfirst_status()
- `clawdbot/design_sources.py` -- Added _fetch_component() wrapper and async resolve_design_sources_with_components() (60 new lines)
- `tests/test_twentyfirst_client.py` -- 7 test cases covering success, degradation, errors, truncation, status
- `tests/test_design_sources.py` -- Extended from 1 to 8 test cases covering enrichment, truncation, max fetch limits, backward compatibility

## Decisions Made
- Direct REST calls to magic.21st.dev instead of MCP protocol (simpler, mirrors recraft_client.py pattern)
- Max 2 component fetches per curated source to stay within API rate limits
- Component code framed as "structural inspiration" with explicit React/TSX adaptation rule
- Thin `_fetch_component` wrapper enables clean test mocking without httpx mock complexity
- Conditional import of twentyfirst_client inside async function to avoid import errors when client unavailable

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

**TWENTYFIRST_API_KEY** environment variable must be set for component fetching to be active. Without it, the system gracefully degrades to existing text-only behavior (zero regression). See 21st.dev for API key provisioning.

## Known Stubs

None -- all functions are fully implemented with proper fallback behavior.

## Next Phase Readiness
- Client and enrichment are ready for Plan 02 (Recraft pipeline wiring)
- `resolve_design_sources_with_components()` can be called from site_builder.py's `_generate_reference_strategy()` in Plan 02
- No blockers

## Self-Check: PASSED

- All 5 files exist on disk
- All 4 task commits found in git history
- 15/15 tests pass

---
*Phase: 10-21st-dev-recraft-pipeline-integration*
*Completed: 2026-03-30*
