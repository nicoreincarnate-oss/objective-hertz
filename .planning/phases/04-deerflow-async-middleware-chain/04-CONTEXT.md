---
phase: 4
name: DeerFlow Async Middleware Chain
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 4: DeerFlow Async Middleware Chain — Context

## Phase Boundary

Cross-cutting middleware pipeline for all Titan stages. Memory, DNA, anti-slop, and telemetry as composable layers.

Requirements: MW-01 through MW-08
Feature flag: ENABLE_MIDDLEWARE
Dependencies: Phase 0b (async engine), Phase 1 (DNA), Phase 2 (anti-slop), Phase 3 (memory)

## Canonical References

- `.planning/phases/4/PLAN.md` — Full task breakdown (Tasks 1-9)
- `shared/contracts.py` — Middleware Protocol
- `openjarvis/workflow/engine.py` — WorkflowEngine to wrap with middleware
- `shared/agent_dna.py` — DNA for guard middleware
- `shared/anti_slop.py` — Anti-slop for quality middleware
- `shared/daemon_memory.py` — Memory for context middleware
- `titan/workflow_pipeline.py` — Titan pipeline stages
