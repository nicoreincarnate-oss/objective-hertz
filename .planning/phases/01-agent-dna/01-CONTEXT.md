---
phase: 1
name: Agent DNA
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 1: Agent DNA — Context

## Phase Boundary

Universal engineering principles injected into every daemon's LLM calls via opt-in system parameter.

Requirements: DNA-01, DNA-02, DNA-03, DNA-04, DNA-05
Feature flag: ENABLE_DNA_PROFILES
Dependencies: Phase 0a (signed manifests), Phase 0b (contracts)

## Canonical References

- `.planning/phases/1/PLAN.md` — Full task breakdown (Tasks 1-6)
- `shared/llm_client.py` — generate() to add use_dna parameter
- `shared/contracts.py` — DNAProvider Protocol to implement
- `shared/skill_loader.py` — Signature verification (Phase 0a)
- `soul/` — Existing soul files, DNA profiles go in soul/dna/
- `openjarvis/security/injection_scanner.py` — Validate DNA before injection
