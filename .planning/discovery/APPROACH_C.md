---
name: approach_c_evaluation
description: Evaluation of Approach C (Minimal MVP) for 7 intel integrations into Objective Hertz
type: episodic
confidence: 0.82
source_reliability: 0.90
last_validated: 2026-03-29
decay_half_life_days: 30
domain: intel-integration
pinned: false
version: 1
---

# Approach C — Minimal MVP Evaluation

**Date:** 2026-03-29
**Reference:** INTEL-INTEGRATION-PLANNING.md, DEBATE_SYNTHESIS.md, all 7 intel refs

---

## Summary

6-week, 3-phase MVP. Phase 1 (2w): DNA + Anti-Slop. Phase 2 (2.5w): DeerFlow Memory
+ simplified middleware + partial async WorkflowEngine fix. Phase 3 (1.5w): RLM for
email compose only. Deferred to v2: PQC, HyperAgents adaptive thresholds, full
DeerFlow middleware chain. Conway P0 fixes skipped (tied to PQC deferral).

---

## Scored Criteria

### 1. Time to First Revenue Impact — 8 / 10

Phase 1 ships Anti-Slop in 2 weeks — the only integration that directly gates
revenue. A slop email poisons open rates and sender domain reputation. The
DNA + Anti-Slop combination arrives fast enough to protect the first real campaigns.
Phase 3 RLM (week 6) compounds the gain: emails become specific instead of generic,
driving reply rates higher.

Loses 2 points: simplified middleware means budget enforcement and compliance
coverage remain incomplete for 6 weeks, leaving some pipeline stages unguarded.

### 2. Technical Risk — 7 / 10

Phase 1 is genuinely low risk: DNA is a markdown file + ~20 lines in
`shared/llm_client.py`. Anti-Slop is a fresh Haiku call + gate condition. No new
dependencies. No DB schema changes. Phase 3 (RLM) adds one external dependency,
touches one file, and has bounded blast radius at email compose only.

Phase 2 carries the risk. DEBATE_SYNTHESIS established the async migration as a
standalone phase requiring full call-site audit, sync wrapper, and per-daemon
migration. Approach C collapses it to "just enough" — a phrase that is not a
specification. Underdefined scope + async concurrency = the highest technical risk
in this approach. `recursive-llm` async performance under concurrent Titan cycles is
also unvalidated (flagged as an open question in INTEL-INTEGRATION-PLANNING).

Loses 3 points for the underspecified async partial fix and unvalidated RLM async
behavior. A 1-day spike before Phase 2 would close both gaps but is not called out.

### 3. Budget Efficiency — 9 / 10

$45-90/month vs the full plan's $85-170/month. Phase 1 (DNA + Anti-Slop) is
$20-40/month and the highest-ROI integration in the portfolio. RLM at Phase 3 costs
$0.06-0.08 per email at 1500 emails/month, scoped to one stage. Deferred integrations
incur no cost when deferred. If v1 demonstrates revenue lift, v2's incremental
$40-80/month is justified by data rather than speculation.

Loses 1 point: debugging time from the partial async fix is a cost not captured in
the cost model.

### 4. Implementation Complexity — 8 / 10

Phase 1 touches 4 files max, zero new dependencies, zero DB migrations — smallest
possible auditable surface. Phase 3 adds 1 dependency, modifies 1 file. No Docker
overlays, no meta-agent containers, no ARM64 cmake build chain.

Phase 2 is the complexity wildcard. DEBATE_SYNTHESIS required interface contracts
in Phase 0 to prevent an integration cliff at middleware wiring. Approach C skips
that setup. "Simplified middleware" is undefined: if it means guardrail + budget on
2 pipeline stages that is manageable; if it means lightly re-architecting the
WorkflowEngine DAG it is not.

Loses 2 points for undefined "simplified middleware" scope.

### 5. Rollback Safety — 9 / 10

Every Phase 1 and Phase 3 integration is independently reversible with a single
config change or file revert. DNA: 1-line `use_dna=False`. Anti-Slop: feature flag
off. RLM: revert email_compose.py to flat DB query. Daemon memory: drop 1 table.
No irreversible schema changes exist in v1 at all.

PQC deferral is actually a safety improvement: the `encrypted_keys` table migration
and 30-day dual-key period are the most irreversible operations in the full plan.
Skipping them means v1 has zero permanent state changes.

Loses 1 point: partial async fix touching 30+ call sites is harder to roll back than
either "no async changes" or "full migration." A half-done async migration can leave
the codebase in a worse state than either extreme.

---

## Aggregate Score

| Criterion                 | Score |
|---------------------------|-------|
| Time to first revenue     | 8/10  |
| Technical risk            | 7/10  |
| Budget efficiency         | 9/10  |
| Implementation complexity | 8/10  |
| Rollback safety           | 9/10  |
| **Total**                 | **41/50** |

---

## Single Biggest Strength

**Anti-Slop ships in 2 weeks — the only integration that directly blocks bad revenue
outcomes.**

Every other integration in the portfolio is infrastructure that improves the system
over time. Anti-Slop is different: it is a binary gate between composition and
delivery. One AI-sounding email in a sequence can flag the sender domain, tank open
rates for the entire campaign, and damage sender reputation in ways that compound.
Approach C front-loads this gate combined with DNA compliance enforcement — the
minimum viable system for safe revenue generation — in 14 days. The 16-week full
plan delivers the same Phase 1 result surrounded by 14 weeks of infrastructure work
that has no direct near-term revenue impact.

---

## Single Biggest Weakness

**"Simplified middleware" and "partial async fix" are undefined scope, making Phase 2
a hidden complexity trap.**

DEBATE_SYNTHESIS explicitly separated the async migration (Phase 0b) from middleware
wiring (Phase 4) and required interface contracts as prerequisites. Approach C merges
all three into 2.5 weeks with no boundary definitions. The failure mode: Phase 2 starts,
the async fix scope expands as call sites are discovered, Phase 2 overruns, Phase 3
(the revenue-generating RLM integration) gets compressed or dropped. Meanwhile,
Phase 1's Anti-Slop gate is already live in production sitting on an unstable async
foundation.

Fix: Before Phase 2 begins, define "simplified middleware" as a named list of pipeline
stages covered and "partial async fix" as a specific count of call sites modified. If
that count exceeds 15, promote async migration to its own mini-phase with a separate
feature flag.

---

## What Is Permanently Lost by Deferring PQC and Adaptive Thresholds

### Post-Quantum Cryptography

Deferral is not catastrophic given Conway has no live wallet funds (March 2026), but
the HNDL exposure window accumulates daily. Attackers who capture `.env` data today
can decrypt it when quantum computing matures (~2030-2035). The targeted data is
Stripe, Anthropic, and Instantly API keys — the highest-value secrets in the system.

Nothing is permanently lost if v2 ships before OH scales to live wallet funds.
The permanent loss is: if live funds arrive before PQC does, every transaction during
that window is retrospectively vulnerable.

Mitigation during deferral: rotate API keys every 30 days, use macOS Keychain over
raw `.env`, do not fund Conway wallets until PQC is in place.

### Adaptive Thresholds / HyperAgents

Titan's `expansion.py` continues using hardcoded evaluation thresholds
(`conversion_rate > 0.05`). Skills producing fewer but higher-value leads get wrongly
rejected. The self-improvement loop still works — it just cannot improve HOW it
evaluates, only what it evaluates.

If v2 never ships: OH's expansion engine plateaus at whatever the hardcoded criteria
optimize for. At current scale this is acceptable. At 5,000+ emails/month the
difference between 3% and 4% conversion is material revenue. The self-accelerating
compound improvement that HyperAgents enables — where evaluation criteria themselves
evolve across generations — is permanently foregone.

The honest call: HyperAgents requires a clean-room Thompson sampling implementation,
Docker meta-agent containers, and a `meta_evaluations` table. It is the most complex
integration in the portfolio and the least urgent for a system that has not yet closed
its first sale. Deferral is the right engineering decision for v1.

---

## Recommendation

Approach C scores 41/50. The full 16-week plan scores approximately 35-37/50 on
the same criteria (weaker on budget efficiency, rollback safety, and time to first
revenue). Approach C is the correct choice for current stage.

Implement with one prerequisite: before Phase 2 begins, define "simplified
middleware" and "partial async fix" as concrete deliverables with specific file and
call-site boundaries.

---

*Last updated: 2026-03-29*
