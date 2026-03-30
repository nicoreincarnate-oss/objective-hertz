# Approach B Evaluation: Fast Revenue

**Date:** 2026-03-29
**Evaluator:** Claude Code (Sonnet 4.6)
**Context:** Approach B is one of two candidate integration strategies for 7 intel references into Objective Hertz.

---

## Phase Sequence

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1 | 1.5w | Anti-Slop quality gate (email + site copy) |
| 2 | 2w | RLM recursive compose + Anti-Slop integration |
| 3 | 1.5w | Agent DNA profiles |
| 4 | 2w | DeerFlow Memory + Middleware (combined) |
| 5 | 1.5w | PQC for Conway |
| 6 | 1.5w | Adaptive Thresholds (Thompson sampling) |
| **Total** | **~10 weeks** | P0 fixes inline per phase |

---

## Scored Criteria

### 1. Time to First Revenue Impact — 9/10

Anti-Slop ships at week 1.5. This is the fastest possible path to measurable improvement.
Email open rates and reply rates are the primary leading indicators for the revenue pipeline.
If current emails are AI-sounding (and the INTEL-INTEGRATION-PLANNING.md says they are — "AI-sounding
emails tank open rates"), then every week of delay has a quantifiable cost.

RLM ships at week 3.5 (end of Phase 2), compounding on the quality lift. By week 4, both
quality filtering AND richer context are active. This is aggressive sequencing that pays off
in this specific system because Titan's email_compose.py is the primary value-generation choke
point.

The 9 (not 10) reflects that P0 bug fixes are deferred inline rather than upfront, meaning
Phase 1 may encounter blocking issues mid-flight.

### 2. Technical Risk — 5/10

This is Approach B's core weakness. Three compounding risks:

**Integration cliff at Phase 4.** Anti-Slop (Phase 1), RLM (Phase 2), DNA (Phase 3) are all
built in isolation without a shared interface contract layer. The Debate Synthesis (DEBATE_SYNTHESIS.md)
identified this exact failure mode: components built independently then wired together in Phase 4
create an integration cliff. Approach A's Phase 0b addressed this with `shared/contracts.py`
before any components were built. Approach B has no equivalent.

**P0 bugs as inline prerequisites.** The Conway wallet column swap, keystore password bug, JSONB
write failures, and SQL injection vectors are not fixed before Phase 1. Each phase absorbs its
relevant P0 fix, but this creates a moving-target environment. If a P0 bug surfaces in Phase 1
that wasn't anticipated as a Phase 1 prerequisite, the phase either stalls or ships with a live
defect.

**RLM async risk is not isolated.** Phase 2 couples RLM (async-heavy, new dependency) with
Anti-Slop integration. If `recursive-llm` has async performance issues under concurrent Titan
pipeline cycles (an open question in INTEL-INTEGRATION-PLANNING.md), the Phase 2 failure contaminates
the Phase 1 work already shipped. In Approach A, the async migration happens first in Phase 0b,
eliminating this class of risk for all subsequent phases.

**DeerFlow Memory + Middleware combined in Phase 4.** Combining two distinct subsystems into one
phase doubles the surface area of things that can go wrong and makes failures harder to bisect.

### 3. Budget Efficiency — 8/10

The cost model is identical to Approach A: $85-170/month delta at 1500 emails/month. Approach B
does not change the cost of any individual component.

The efficiency argument for B is that revenue-impacting phases ship earlier, so the ROI timeline
is compressed. If Anti-Slop genuinely improves open rates by even 5%, the campaign economics
improve within the first sprint. This means the incremental cost ($15-30/month for Anti-Slop
scoring) is offset faster.

The caveat: if Phase 1 Anti-Slop ships without DNA (Phase 3 is deferred), the quality gate
runs without the compliance and engineering-principle context that DNA provides. The anti-slop
scorer and the LLM composer are not operating from the same principles. This could produce
higher naturalness scores while still violating CAN-SPAM, since those rules aren't enforced
until Phase 3.

Score of 8 reflects faster ROI realization, docked for the compliance gap.

### 4. Implementation Complexity — 5/10

Phase 4 (DeerFlow Memory + Middleware combined) is the most complex single phase in either
approach. Approach A separates these into Phase 3 (Memory, 2 weeks) and Phase 4 (Middleware,
2 weeks) with a buffer week between them. Combining them into one 2-week phase means:

- The middleware chain (guardrail, budget, summarization, memory, subagent limit) must be
  built at the same time as the persistent memory system it depends on.
- The `daemon_memory` table, JSON cache write-through pattern, and startup injection all need
  to be stable before the memory middleware can be tested.
- No buffer week to resolve cross-phase integration issues before adding the middleware layer.

The early phases (1-3) are individually simpler than their Approach A equivalents. Anti-Slop
in isolation is well-scoped. RLM + Anti-Slop integration is manageable at 2 weeks. But the
complexity that Approach A distributes evenly gets compressed into Phase 4.

### 5. Rollback Safety — 6/10

Each phase has a feature flag, which is correct. Rolling back any individual phase is safe in
isolation.

The concern is the dependency chain. Phase 2 (RLM) requires Anti-Slop quality scoring as its
evaluation mechanism. If Phase 1 is rolled back (say, Anti-Slop is producing false negatives
and blocking legitimate emails), Phase 2 loses its evaluation layer mid-phase. In Approach A,
DNA and Anti-Slop are separate phases with independent rollback paths, and RLM comes later
with both firmly established.

The combined Phase 4 (Memory + Middleware) has a compounded rollback story: rolling back
middleware requires verifying memory middleware is also safe to remove, since the two were
built and tested together. Bisecting a rollback across a combined phase is harder.

30-day dual-key for PQC (Phase 5) remains safe regardless of approach ordering. PQC rollback
safety is unchanged.

---

## Summary Scores

| Criterion | Score | Notes |
|-----------|-------|-------|
| Time to first revenue impact | 9/10 | Anti-Slop at week 1.5 is maximum speed |
| Technical risk | 5/10 | No contracts layer, P0 inline, combined Phase 4 |
| Budget efficiency | 8/10 | Faster ROI offset, compliance gap is a caveat |
| Implementation complexity | 5/10 | Phase 4 is high-complexity, poor risk distribution |
| Rollback safety | 6/10 | Feature flags present, dependency chain is fragile |
| **Total** | **33/50** | vs Approach A's estimated ~37/50 |

---

## Single Biggest Strength

**The revenue sequencing is correct.** Anti-Slop is genuinely the highest-leverage first move.
It has zero prerequisites (no new DB tables on the critical path, no async changes required, no
new dependencies that could fail to build). It directly addresses the "AI-sounding emails tank
open rates" problem that the INTEL-INTEGRATION-PLANNING.md names as a primary deficiency.
Shipping it first means that every subsequent phase — RLM, DNA, Memory — is tested against a
system that already has quality filtering in place. The quality baseline is established early,
which makes later phases easier to evaluate.

This reordering reflects a genuine insight: not all technical prerequisites need to ship before
revenue drivers. Anti-Slop is independent. Starting with it is a valid engineering choice, not
just business impatience.

---

## Single Biggest Weakness

**No interface contracts before component build.** The Debate Synthesis already identified the
integration cliff at Phase 4 as a critical finding in the corrected Approach A. Approach B
reproduces this risk exactly: Anti-Slop, RLM, and DNA are built across Phases 1-3 without a
shared `shared/contracts.py` Protocol layer defining `SlopScorer`, `DNAProvider`, `MemoryStore`,
and `Middleware` interfaces. When Phase 4 wires them together, each component has implicitly
defined its own interface. The integration work in Phase 4 is not just wiring — it's discovering
and resolving interface mismatches that could have been specified upfront in a single day.

This is the difference between 2 weeks of planned middleware work and 3-4 weeks of debugging
why the anti-slop scorer's output format doesn't match what the middleware chain expects. In a
system like Objective Hertz where the components are built by LLMs (AI-coder failure modes are
documented in project_ai_coder_failures.md), implicit interfaces are a known failure vector.

---

## 8-Week Compression: What Gets Cut

Compressing from 10 to 8 weeks requires removing 2 weeks. The viable cuts, in order of
preference:

**Cut 1: Defer Adaptive Thresholds (Phase 6, 1.5w) to v2.**
This is the obvious first cut. Thompson sampling adaptive expansion improves self-improvement
quality but has no direct revenue impact in the first sales cycles. The existing hardcoded
thresholds continue to function. Revenue is not blocked. Estimated timeline: 8.5 weeks (still
slightly over, but one phase is complete).

**Cut 2: Reduce Phase 4 to Middleware only; defer DeerFlow Memory to v2.**
Persistent daemon memory (the DeerFlow pattern) reduces cold-start planning overhead and is
valuable, but does not directly affect email quality or conversion. The middleware chain is
the higher-priority half of the combined phase. Memory can be added standalone in a later cycle
without touching the middleware architecture. This brings the timeline to approximately 7.5 weeks
and eliminates the high-complexity combined phase.

**What must NOT be cut in an 8-week schedule:**
- Anti-Slop (Phase 1) — primary revenue driver
- RLM (Phase 2) — secondary revenue driver, compounding on Anti-Slop
- DNA (Phase 3) — compliance is a legal requirement, not optional
- PQC (Phase 5) — Conway wallet key protection, security non-negotiable once live funds exist

The 8-week schedule is: Anti-Slop (1.5w) → RLM + Integration (2w) → DNA (1.5w) → Middleware
only (2w) → PQC (1.5w) = 8.5 weeks. Achievable with tight execution and no blocking P0 bugs.

---

## Recommendation

Approach B is the right choice if the operator's primary constraint is time-to-revenue and the
team can accept higher integration risk. The recommended mitigation is to prepend a 3-day sprint
before Phase 1 that produces only `shared/contracts.py` — nothing else. This eliminates the
integration cliff without meaningful timeline impact and converts the 33/50 score to approximately
37/50, matching Approach A's safety profile while retaining Approach B's revenue sequencing.

Without that contracts sprint, Approach B trades a measurable risk increase for a 6-week schedule
advantage. Whether that trade is worth it depends on how urgently the first revenue event matters.

---

*Evaluated: 2026-03-29 | Context: INTEL-INTEGRATION-PLANNING.md + DEBATE_SYNTHESIS.md*
