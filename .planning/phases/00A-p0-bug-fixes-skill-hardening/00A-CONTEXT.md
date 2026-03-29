# Phase 0a: P0 Bug Fixes + Skill Hardening - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss)

<domain>
## Phase Boundary

Fix Conway wallet critical bugs and harden skill loader trust boundary before any integration work.

Requirements: FIX-01, FIX-02, FIX-03, FIX-04

Success criteria:
- Wallet round-trip test: create, write, read back, verify column correctness
- Keystore cannot be decrypted with agent name
- Survival tier JSONB writes persist and read back correctly
- Skill loader rejects unsigned files, accepts signed ones

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — discuss phase was skipped per user setting. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Detailed implementation plan exists at `.planning/phases/0a/PLAN.md` — the planner should read this for comprehensive task breakdown, code references, and acceptance criteria.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Mega-Plan Phase Detail
- `.planning/phases/0a/PLAN.md` — Detailed task breakdown with code line references, acceptance criteria, and risk register

### Conway Wallet
- `conway/wallet.py` — Wallet creation, keystore encryption/decryption
- `conway/survival.py` — Survival tier JSONB writes
- `scripts/migrations/007-conway-tables.sql` — conway_wallets schema

### Skill Loader
- `shared/skill_loader.py` — Current skill loading with zero verification

### Requirements
- `.planning/REQUIREMENTS.md` — FIX-01 through FIX-04 definitions

</canonical_refs>

<code_context>
## Existing Code Insights

Codebase context will be gathered during plan-phase research.

</code_context>

<specifics>
## Specific Ideas

No specific requirements — discuss phase skipped. Refer to ROADMAP phase description, success criteria, and `.planning/phases/0a/PLAN.md` for comprehensive implementation details.

</specifics>

<deferred>
## Deferred Ideas

None — discuss phase skipped.

</deferred>

---

*Phase: 00A-p0-bug-fixes-skill-hardening*
*Context gathered: 2026-03-29 via autonomous mode (discuss skipped)*
