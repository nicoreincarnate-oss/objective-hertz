---
phase: 2
name: Anti-Slop Quality Gate
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 2: Anti-Slop Quality Gate — Context

## Phase Boundary

Every outbound text (email, site copy, alert) scored for quality before dispatch. Slop detected and rewritten.

Requirements: SLOP-01 through SLOP-08
Feature flag: ENABLE_ANTI_SLOP
Dependencies: Phase 0b (SlopScorer Protocol)

## Canonical References

- `.planning/phases/2/PLAN.md` — Full task breakdown (Tasks 1-8)
- `shared/contracts.py` — SlopScorer Protocol to implement
- `titan/pipeline/email_compose.py` — Email pipeline to gate
- `clawdbot/site_builder.py` — Site copy to gate
- `shared/llm_client.py` — LLM client for Haiku scoring calls
- `intel/anti-slop/` — Reference patterns and research
