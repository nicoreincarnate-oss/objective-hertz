# Code Optimization Audit & Fix

## Goal
Reduce technical debt, eliminate dead code, consolidate duplicated patterns, and fix performance inefficiencies — while preserving 100% identical runtime behavior. Zero feature changes.

## Constraint
Every optimization must be provably behavior-preserving. No feature removals, no API changes, no behavioral modifications.

## System Under Optimization
Objective Hertz: 5 daemons (Orchestrator, Perseus, Titan, Hermes, ClawdBot), ~235K LOC total, ~450 main Python files, async-first architecture on Postgres + Claude API.

## Pipeline Results

### Stage 0: Memory Context
- 6 memory files loaded (architecture, bugs, failures, components, fix priorities, integration)
- Prior audit (March 2026) identified 32 bugs, 12 AI-coder failure patterns, dead code

### Stage 1: Codebase Mapping (4 parallel agents)
- **File sizes**: 235K LOC, top files are vendored (firecrawl 5.2K, browser-use 4K), project hotspots are clawdbot/daemon.py (1.9K), titan/memory.py (1.9K), site_builder.py (1.8K)
- **Dead code**: 8 shared modules test-only (~1.2K LOC), 4 tmp files (134 LOC), 1 backup (4.6K LOC), 300+ openjarvis files unused externally (~80K LOC)
- **Duplication**: 23+ JSON extraction instances, 3x task queue, 4x daemon init, 40+ error handling duplications
- **Performance**: sync firecrawl blocking async, LLM cost estimation wrong, trace analyzer N+1, 20+ silent exceptions

### Stage 2: Discovery (3 parallel agents)
- **Dead code risk**: mem0_compat_service.py is LIVE (Docker), don't touch. 8 shared modules confirmed safe. 3 previously-dead modules already removed.
- **DRY approach**: No existing JSON utility in shared/. agent_base.py needs 2 new methods. ~360 lines consolidatable.
- **Performance**: LLM cost fix is 30 LOC (needs usage threading). Firecrawl is sync requests (50 LOC to httpx). ThreadPool was already bounded (false alarm).

### Stage 3: Alpha-Beta Debate
- Alpha proposed 5 phases, Beta corrected ordering (observability before risky refactors) and reduced JSON scope (10-12 sites, not 23)
- Key exclusions: No openjarvis cleanup (architectural value), no daemon DRY (premature abstraction risk), no pipeline async (too risky for cleanup pass)

### Stage 4: Think-at-n Evaluation
- Approach A (Conservative 5-phase): 7.8/10 composite — WINNER
- Approach B (Aggressive 6-phase): 6.8/10 — too risky for zero-regression constraint
- Approach C (Minimal 3-phase): 7.1/10 — leaves too much on table
- Modification from B: add test-only module relocation (zero risk)

## Phase Summary

| Phase | Name | LOC Change | Risk | Key Deliverable |
|-------|------|-----------|------|-----------------|
| 1 | Dead Code Removal + Lazy Imports | -4,750 | LOW | Clean root, remove backups, fix imports |
| 2 | Cost Estimation + Observability | +40 | LOW | Accurate budget, slow query logging |
| 3 | Exception Hardening | +25 | LOW | Visible failures at 20+ silent locations |
| 4 | JSON Extraction Consolidation | -100 | MED-LOW | shared/json_utils.py, 10-12 sites migrated |
| 5 | Firecrawl Sync-to-Async | ~50 | MEDIUM | Event loop unblocked for web scraping |

## Validation Strategy
- Every phase: `ruff check` + full pytest suite
- Every phase: separate git commit (selective revert without losing later phases)
- Phase 4+5: manual review of each modified call site
- No phase proceeds until previous phase passes all tests

## Explicit Exclusions (with rationale)
- **openjarvis/ 300+ files**: Framework scaffolding, provides extensibility. Not worth removing.
- **Daemon init/task queue DRY**: Legitimate behavioral differences across daemons. Premature abstraction risk.
- **run_pipeline() async conversion**: Core revenue path, needs dedicated effort.
- **Trace analyzer SQL optimization**: Working correctly, high effort, not a correctness issue.
- **Complex JSON extraction sites**: Sites with retry logic, multi-block extraction left for future refactor.
