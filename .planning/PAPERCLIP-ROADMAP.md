# Paperclip Infrastructure Integration — Roadmap

**Created:** 2026-03-30
**Approach:** Approach B (Safety-First) — Budget consolidation first, then layered integration
**Source:** Paperclip (MIT license) — 18 patterns selected from 27 analyzed
**Total phases:** 6 (Phases 11-16, continuing intel-integration milestone)

## Debate Synthesis

Alpha proposed 4-phase sprint ordering matching Paperclip's categories. Beta found 21 weaknesses:
- Budget checking exists in 4 places → consolidate to ONE authority first (Phase 0/11)
- Adapter interface is over-engineering → skip, extend AgentBase instead
- Org hierarchy solves no AEGIS finding → replace with governance/approvals
- Promptfoo adds a second test framework → use pytest behavioral evals instead
- Log redaction closes an AEGIS credential finding → add to Phase 14
- Wakeup queue is destructive → isolate as final phase

Think-at-n evaluated 3 approaches: A (40/50), **B (43/50)**, C (33/50). Approach B won on risk management and integration cleanliness.

## Pattern Accounting (18 total)

| # | Pattern | Source | Phase | Status |
|---|---------|--------|-------|--------|
| 1 | Agent State Machine | Paperclip constants.ts | 12 | Planned |
| 2 | Atomic Task Checkout | Paperclip checkoutRunId | 12 | Planned |
| 3 | Recursion Guard | Paperclip requestDepth | 12 | Planned |
| 4 | Forbidden Token Scanner | Paperclip check-forbidden-tokens | 12 | Planned |
| 5 | Session Health Tracking | Paperclip session-compaction | 12 | Planned |
| 6 | Budget Gate Before Execution | Paperclip getInvocationBlock | 13 | Planned |
| 7 | Multi-Scope Budget Policies | Paperclip budget_policies | 13 | Planned |
| 8 | Per-LLM-Call Cost Events | Paperclip cost_events | 13 | Planned |
| 9 | Pytest Behavioral Evals | Inspired by Paperclip promptfoo | 14 | Planned |
| 10 | Heartbeat Lifecycle | Paperclip SKILL.md heartbeat | 14 | Planned |
| 11 | Log Redaction | Paperclip log redaction + AEGIS | 14 | Planned |
| 12 | Goal Cascade | Paperclip goals.ts | 15 | Planned |
| 13 | Governance/Approvals | Paperclip approvals.ts | 15 | Planned |
| 14 | Commit Metrics Tracker | Paperclip commit-metrics | 15 | Planned |
| 15 | Event-Driven Wakeup Queue | Paperclip wakeup_requests | 16 | Planned |
| — | Budget Consolidation (prereq) | Debate correction | 11 | Planned |
| — | Adapter Interface | DROPPED (Beta W8) | — | AgentBase is already the adapter |
| — | Org Hierarchy | REPLACED by #13 | — | Governance closes AEGIS finding |

## Phase Dependency Graph

```
Phase 11 (Budget Consolidation)
    │
    ├──→ Phase 12 (Foundation: state machine, checkout, recursion, tokens, health)
    │       │
    │       └──→ Phase 13 (Budget: gate, policies, cost events)
    │               │
    │               └──→ Phase 14 (Quality: evals, heartbeat, log redaction)
    │                       │
    │                       └──→ Phase 15 (Architecture: goals, governance, metrics)
    │                               │
    │                               └──→ Phase 16 (Event Wakeup — isolated, highest risk)
    │
    └──→ [All phases depend on 11 being complete]
```

## Phase Details

### Phase 11: Budget Consolidation
**Goal:** Consolidate 4 budget check surfaces into 1 middleware authority. Fix AEGIS fail-open bug.
**Patterns:** Prerequisite (no new Paperclip patterns)
**Feature flag:** `ENABLE_CONSOLIDATED_BUDGET`
**Migration:** None
**Plans:** 3 sub-plans (306 lines)
**Risk:** Zero-enforcement window during cutover → two-commit strategy

### Phase 12: Foundation Patterns
**Goal:** Agent state machine, atomic checkout, recursion guard, forbidden tokens, session health.
**Patterns:** 1-5
**Feature flags:** 5 flags, all default OFF
**Migration:** 025 (agent_registry columns, task_queue depth, session_health table)
**Plans:** 5 patterns (775 lines)
**Risk:** SKIP LOCKED latency on hot table → benchmark first

### Phase 13: Budget & Cost Patterns
**Goal:** Pre-execution budget gate, multi-scope policies, per-call cost events.
**Patterns:** 6-8
**Feature flags:** 3 flags, all default OFF, 48hr shadow mode
**Migration:** 026 (budget_policies + cost_events tables, seeded with $800/month default)
**Plans:** 6 tasks (854 lines)
**Risk:** Empty policy table blocks everything → default policy seeded in migration

### Phase 14: Quality & Observability
**Goal:** Behavioral evals, heartbeat lifecycle, log redaction.
**Patterns:** 9-11
**Feature flags:** 3 flags, all default OFF
**Migration:** 027 (eval_results table)
**Plans:** 13 tasks (1,268 lines)
**Risk:** Log redaction latency → <1ms per line target, benchmark

### Phase 15: Architecture — Additive
**Goal:** Goal cascade, governance/approvals, commit metrics.
**Patterns:** 12-14
**Feature flags:** 3 flags, all default OFF
**Migration:** 028 (goals, approvals, commit_metrics tables + seed data)
**Plans:** 3 sub-plans (1,311 lines)
**Risk:** Goal cascade underscoped → start as lightweight tagging (VARCHAR, not FK)

### Phase 16: Event-Driven Wakeup Queue
**Goal:** Replace fixed polling with event-driven LISTEN/NOTIFY + fallback polling.
**Patterns:** 15
**Feature flags:** 3-mode flag: off/shadow/true
**Migration:** 029 (wakeup_subscriptions, wakeup_requests, trigger function)
**Plans:** 8 sub-plans (1,601 lines)
**Risk:** Most dangerous phase → isolated, dedicated LISTEN connection outside pool, fallback poll NEVER removed

## Migration Sequence

| Number | Phase | Tables/Changes |
|--------|-------|---------------|
| 025 | 12 | agent_registry +state/pause_reason, task_queue +depth, session_health |
| 026 | 13 | budget_policies (seeded), cost_events + indices |
| 027 | 14 | eval_results |
| 028 | 15 | goals (seeded), approvals, commit_metrics, task_queue +goal_tag |
| 029 | 16 | wakeup_subscriptions (seeded), wakeup_requests, notify trigger |

## Feature Flag Summary

| Flag | Phase | Default | Scope |
|------|-------|---------|-------|
| ENABLE_CONSOLIDATED_BUDGET | 11 | false | Budget enforcement |
| AGENT_STATE_MACHINE_ENABLED | 12 | false | Agent lifecycle |
| ATOMIC_CHECKOUT_ENABLED | 12 | false | Task checkout |
| RECURSION_GUARD_ENABLED | 12 | false | Task depth |
| FORBIDDEN_TOKEN_SCAN_ENABLED | 12 | false | Output sanitization |
| SESSION_HEALTH_ENABLED | 12 | false | Session tracking |
| PRE_EXECUTION_BUDGET_GATE_ENABLED | 13 | false | Cost estimation |
| MULTI_SCOPE_BUDGET_ENABLED | 13 | false | Per-agent budgets |
| PER_CALL_COST_EVENTS_ENABLED | 13 | false | Cost attribution |
| BEHAVIORAL_EVALS_ENABLED | 14 | false | Eval recording |
| HEARTBEAT_LIFECYCLE_ENABLED | 14 | false | Heartbeat emission |
| LOG_REDACTION_ENABLED | 14 | false | Secret scrubbing |
| GOAL_CASCADE_ENABLED | 15 | false | Goal hierarchy |
| GOVERNANCE_ENABLED | 15 | false | Approval system |
| COMMIT_METRICS_ENABLED | 15 | false | Git analytics |
| EVENT_WAKEUP_ENABLED | 16 | off | off/shadow/true |

## Estimated New Code

| Category | Lines |
|----------|-------|
| New Python modules | ~1,500 |
| Modifications to existing | ~400 |
| New tests | ~150 tests |
| SQL migrations | ~250 |
| Soul/lifecycle docs | ~500 |
| **Total** | **~2,650** |

## Execution Strategy

After each phase: `ruff check` + `pytest` + `/review` + `/qa`
After all phases: `/ship` to create PR
Feature flags enable incremental rollout per pattern

---
*Roadmap created: 2026-03-30 — Mega-plan pipeline (debate winner: Approach B, score 43/50)*
