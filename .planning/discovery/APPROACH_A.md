# Approach A Evaluation — Sequential 10-Phase Intel Integration

**Date:** 2026-03-29
**Evaluator:** Claude Code (Sonnet 4.6)
**Input:** DEBATE_SYNTHESIS.md + INTEL-INTEGRATION-PLANNING.md

---

## Scoring Summary

| Criterion | Score | Rationale |
|-----------|-------|-----------|
| Time to first revenue impact | 6/10 | Anti-slop gate ships at Phase 2 (~week 4-5), but emails use it fully only after Phase 4 middleware wiring (~week 9) |
| Technical risk | 7/10 | Feature flags + interface contracts reduce risk substantially; async migration (Phase 0b) is the highest-risk moment |
| Budget efficiency | 8/10 | $85-170/month increase against $800 budget is well-proportioned; cost is back-loaded to later phases |
| Implementation complexity | 7/10 | Each phase has a single clear concern; Phase 4 (middleware wiring) is the most complex single phase |
| Rollback safety | 9/10 | Feature flags on all phases, 30-day dual-key PQC window, shadow scoring before blocking — rollback is low-cost |

**Composite: 37/50 (74%)**

---

## Criterion 1: Time to First Revenue Impact — 6/10

**Best case:** Anti-slop quality scoring goes live at Phase 2 (~week 4.5 from start). If the shadow-mode scoring reveals the existing email quality baseline without blocking sends, you have your first diagnostic signal early. That's useful data even before the gate enforces anything.

**Realistic case:** The anti-slop gate only *blocks* low-quality emails after Phase 4 wires it into the mandatory middleware chain (~week 9-10). Until Phase 4, anti-slop can flag but not intercept. The revenue lever — emails that are meaningfully better because they use RLM context — doesn't close until Phase 5 (~week 11-13).

**The gap:** Phases 0a, 0b, and 1 (infrastructure + async + DNA) produce zero direct email quality improvement. That's roughly 3.5 weeks of setup before the first user-facing feature ships. For an autonomous revenue system, that's an acceptable trade-off if the foundation work prevents integration failures later — but it is a trade-off.

**What would improve this:** Moving Anti-Slop (Phase 2) to run parallel with Phase 1 would cut 1.5 weeks off first-impact timeline. The dependency is weak: Phase 1 (DNA) injects context into LLM calls; Phase 2 (Anti-Slop) scores outputs. They don't actually depend on each other.

---

## Criterion 2: Technical Risk — 7/10

**Highest-risk phase: Phase 0b (async migration)**
Converting the Titan pipeline from sync-blocking to async WorkflowEngine with backward-compat wrappers is the riskiest single operation. It touches every stage. Rollback means re-converting to sync, which is painful. The debate synthesis correctly isolated this as its own phase, which is the right call — but it doesn't eliminate the risk.

**Second-highest risk: Phase 4 (middleware wiring)**
Phase 4 integrates DNA (Phase 1), Anti-Slop (Phase 2), and Memory (Phase 3) into a mandatory chain. Any of those three having subtle bugs will surface here. The 1-week buffer after Phase 3 exists precisely for this, but it may not be enough. If Phase 2 or Phase 3 has a behavioral edge case that doesn't manifest until all three run in combination, the buffer absorbs it — unless there are multiple issues.

**Risk mitigations that are solid:**
- Interface contracts (`shared/contracts.py`) defined in Phase 0b mean Phase 4 wires to stable Protocol types, not live implementations. This prevents the "component built in isolation, breaks at integration" failure.
- Feature flags on every phase mean production impact is toggleable. Worst case: flip a flag, revert to baseline.
- PQC ARM64 spike before Phase 6 validates the build environment before committing to the full implementation. If `liboqs-python` fails on M4, the fallback plan to `pqcrypto` is already named.

**Risk the approach doesn't fully address:**
- RLM recursive sub-calls (Phase 5) under concurrent Titan pipeline cycles. The planning doc flags this as an open question. If the `recursive-llm` library doesn't handle concurrent callers cleanly, Phase 5 could introduce pipeline contention. The $100/month monthly RLM cap helps with cost but doesn't address concurrency.

---

## Criterion 3: Budget Efficiency — 8/10

**Cost model is realistic.** The debate synthesis corrected Alpha's original underestimation:
- Per-email cap ($0.08) prevents runaway RLM costs at scale
- Haiku for evaluation/scoring reduces the most frequent operation costs
- Monthly $100 RLM cap with automatic fallback is a hard ceiling
- PQC is computational-only — $0 incremental cost

**Value delivered per dollar:**
- Anti-Slop ($15-30/month) → directly defends open rates and brand reputation. If open rates improve by even 2%, the revenue impact far outpaces $30/month.
- RLM ($45-75/month) → email specificity increase is the highest-leverage email quality intervention available. Hard to quantify ROI before testing, but the investment is bounded.
- DNA ($5-10/month) → compliance and quality inheritance for all future skills costs nearly nothing. High value per dollar.
- Memory ($10-20/month) → reduces cold-start planning overhead. Moderate value.

**One concern:** The $85-170/month range is wide. At 16 weeks, the total cost exposure before revenue impact validates is $340-680 in API overhead (assuming costs start accumulating when phases go live). That's not a blocker — it's within the $800 budget — but the team should set a 4-week review gate: if Phase 2 anti-slop scores don't show measurable quality improvement by week 6, the cost case for continuing to Phase 5 (RLM) deserves re-evaluation.

---

## Criterion 4: Implementation Complexity — 7/10

**Per-phase complexity is well-managed.** Each phase has one primary deliverable:
- Phase 0a: Bug fixes (known scope)
- Phase 0b: Async migration (known scope, well-documented risk)
- Phase 1: DNA profiles (additive, no existing code changes required)
- Phase 2: Anti-slop scorer (new module + one pipeline insertion point)
- Phase 3: 3-tier memory (new table + JSON cache + startup injection)
- Phase 4: Middleware chain wiring (integration phase — inherently more complex)
- Phase 5: RLM integration (new dependency + one compose function replacement)
- Phase 6: PQC (new cryptographic module + migration script)
- Phase 7: Adaptive thresholds (Thompson sampling on existing expansion.py)

**Phase 4 is the complexity peak.** It takes three independently-built components and makes them mandatory for all pipeline stages. The complexity multiplies — not just three things to debug, but three things interacting. The 1-week buffer before Phase 4 starts is the right defensive move, but Phase 4 itself should plan for 2 weeks, not 2. It's underestimated in the synthesis.

**Concurrent concerns across the full timeline:** Low. Each phase is one concern. The interface contracts mean Phase 4 doesn't need to understand the internals of Phases 1-3 — only their contracts. This is the approach's best structural decision.

---

## Criterion 5: Rollback Safety — 9/10

This is where Approach A genuinely excels.

**Every phase has a named rollback path:**
- DNA: Remove `use_dna=True` parameter calls. Zero database changes.
- Anti-Slop: Drop `quality_scores` table, remove gate call. One insertion point.
- Memory: Drop `daemon_memory` table, remove JSON cache, remove startup injection. Clean.
- Middleware: Restore direct stage calls. Remove chain initialization.
- RLM: Revert `email_compose.py` to flat DB query. One function.
- PQC: 30-day dual-key period means both legacy and PQ keys work simultaneously. Rollback = stop writing PQ-encrypted keys and expire them after the dual period.
- Adaptive Thresholds: Revert `expansion.py` to hardcoded criteria. One file.

**Feature flags make rollback operational, not surgical.** You don't need to redeploy to roll back — flip the flag in daemon config. This is production-grade operational safety.

**The PQC rollback deserves special mention:** 30 days of dual-key validity gives Conway wallet operations zero-downtime rollback window. This was Beta's correction applied correctly. If the PQ decryption fails in production (e.g., due to M4 ARM64 edge case), the legacy keys are still valid.

**Why not 10/10:** Phase 0b (async migration) has no clean rollback once the call sites are converted. The backward-compat sync wrappers help, but converting back is effectively re-doing the phase in reverse. That's the one phase where "rollback" means "re-implement." It's also the riskiest phase — those two facts compound.

---

## Biggest Strength

**Interface contracts defined upfront (Phase 0b).**

`shared/contracts.py` with Protocol types for `DNAProvider`, `SlopScorer`, `MemoryStore`, and `Middleware` is the single decision that prevents Phase 4 from becoming an integration crisis. Without contracts, Phases 1-3 each build to their own internal interfaces. Phase 4 then has to reverse-engineer those interfaces while simultaneously wiring them together. With contracts, Phase 4 is just plumbing — the shapes are already agreed upon.

This was Beta's correction to Alpha, and it's the correction that most improves the overall plan's probability of success.

---

## Biggest Weakness

**Phase 5 (RLM) depends on an untested external library under concurrent load.**

The `recursive-llm` library's behavior under concurrent Titan pipeline cycles is flagged as an open question in the planning doc but not addressed in the phase plan. Phase 5 arrives at week 11-13, after $300-400 in accumulated API costs and significant implementation investment. If `recursive-llm` has concurrency issues (shared state, async context bleed, rate-limit collisions when multiple pipeline stages call it simultaneously), the phase either gets delayed or ships with a concurrency bug that produces non-deterministic email outputs.

The fix is a 2-day concurrency spike in Phase 0b alongside the async migration. Both concerns are about async behavior under load — they naturally co-locate. Discovering the concurrency limit in week 2 instead of week 12 is worth 2 days of upfront investigation.

---

## What Gets Cut at 10 Weeks

Compressing from 16 to 10 weeks means removing 6 weeks. The two buffer weeks are the first cut — but that leaves 4 more weeks to find.

**What survives (must-haves for first revenue impact):**
- Phase 0a: P0 bug fixes — non-negotiable
- Phase 0b: Async migration + contracts — non-negotiable (everything downstream depends on it)
- Phase 2: Anti-Slop quality gate — highest revenue impact per week invested
- Phase 5: RLM recursive context retrieval — highest email quality improvement

**What gets cut:**
- Phase 1 (DNA Profiles): Cut or merged as a single-day add-on to Phase 0b. DNA is high value per dollar but low direct revenue impact. The compliance and quality principles can be injected manually at first without the formal DNA loader.
- Phase 3 (DeerFlow Memory): Deferred. Memory GC complexity and the daemon restart persistence benefit don't justify the 2-week investment when the goal is 10-week revenue impact.
- Phase 4 (Middleware Chain): Scope reduced to Anti-Slop gate insertion only (Phase 2 already does this). Full middleware chain deferred to post-10-week.
- Phase 6 (PQC): Deferred. Conway has no live wallet funds at this stage. Risk is theoretical.
- Phase 7 (Adaptive Thresholds): Deferred. Thompson sampling improves the self-improvement loop, not email quality directly.

**Resulting 10-week plan:**
Phase 0a (1w) → Phase 0b + concurrency spike (1.5w) → Phase 2 Anti-Slop (1.5w) → Phase 5 RLM (2.5w) → Integration buffer (1w) → Phase 1 DNA add-on (0.5w) → Final stabilization (2w).

Revenue impact arrives at week 4-5 (anti-slop) and week 7-9 (RLM specificity). The deferred phases (Memory, full Middleware, PQC, Adaptive Thresholds) form a natural v2 roadmap.

---

*Evaluation complete. See APPROACH_B.md for comparison if applicable.*
