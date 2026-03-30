---
phase: 0b
name: Async Engine + Interface Contracts + Observability
status: ready
gathered: 2026-03-29
mode: autonomous (discuss skipped)
---

# Phase 0b: Async Engine + Interface Contracts + Observability — Context

## Phase Boundary

Convert WorkflowEngine to async, define integration contracts, establish metrics baseline.

Requirements: ASYNC-01, ASYNC-02, CONTRACT-01, OBS-01

Success criteria:
- WorkflowEngine runs async DAGs without regression
- Sync wrapper works for existing callers
- shared/contracts.py has Protocol types for DNAProvider, SlopScorer, MemoryStore, Middleware
- Baseline metrics dashboard shows LLM calls/latency/errors/cost per daemon

## Implementation Decisions

All implementation choices are at Claude's discretion — discuss phase was skipped per user setting. Use ROADMAP phase goal, success criteria, and codebase conventions to guide decisions.

Detailed implementation plan exists at `.planning/phases/0b/PLAN.md` — the planner should read this for comprehensive task breakdown, code references, and acceptance criteria.

## Canonical References

- `.planning/phases/0b/PLAN.md` — Full task breakdown (Tasks 1-5)
- `openjarvis/workflow/engine.py` — WorkflowEngine (sync, to be made async)
- `titan/workflow_pipeline.py:164` — Primary engine.run() caller
- `shared/llm_client.py` — LLM client to instrument with metrics
- `hermes/web/app.py` — Add GET /api/metrics endpoint
- `scripts/migrations/` — Migration 017 (llm_metrics table)
