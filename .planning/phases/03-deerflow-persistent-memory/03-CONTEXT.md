---
phase: 3
name: DeerFlow Persistent Memory
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 3: DeerFlow Persistent Memory — Context

## Phase Boundary

Daemons remember context across restarts. Three-tier memory with garbage collection.

Requirements: MEM-01 through MEM-08
Feature flag: ENABLE_DEERFLOW_MEMORY
Dependencies: Phase 0b (MemoryStore Protocol), Phase 1 (DNA memory_domains for isolation)

## Canonical References

- `.planning/phases/3/PLAN.md` — Full task breakdown (Tasks 1-8)
- `shared/contracts.py` — MemoryStore Protocol to implement
- `shared/agent_base.py` — AgentBase lifecycle hooks to extend
- `shared/magma.py` — MAGMA for semantic compression
- `soul/dna/*.yaml` — memory_domains field for isolation
- `perseus/scheduler.py` — Add daily cleanup job
