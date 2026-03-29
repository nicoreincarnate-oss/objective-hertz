# Intel Integration Roadmap

**Created:** 2026-03-29
**Approach:** Full Approach A with Beta corrections (8 phases + 2 buffer weeks)
**Total phases:** 10 (8 work + 2 buffer)

## Phase 0a: P0 Bug Fixes + Skill Hardening
**Goal:** Fix Conway wallet critical bugs and harden skill loader trust boundary before any integration work.
**Requirements:** FIX-01, FIX-02, FIX-03, FIX-04
**Feature flag:** N/A (prerequisite fixes)
**Plans:** 2 plans
Plans:
- [x] 00A-01-PLAN.md -- Conway wallet bug regression tests (FIX-01/02/03)
- [x] 00A-02-PLAN.md -- Skill signing implementation and tests (FIX-04)
**Success criteria:**
- [x] Wallet round-trip test: create, write, read back, verify column correctness
- [x] Keystore cannot be decrypted with agent name
- [x] Survival tier JSONB writes persist and read back correctly
- [x] Skill loader rejects unsigned files, accepts signed ones
**Status:** ✅ Complete (2026-03-29) — 25/25 tests passing

## Phase 0b: Async Engine + Interface Contracts + Observability
**Goal:** Convert WorkflowEngine to async, define integration contracts, establish metrics baseline.
**Requirements:** ASYNC-01, ASYNC-02, CONTRACT-01, OBS-01
**Feature flag:** N/A (infrastructure)
**Dependencies:** None
**Success criteria:**
- WorkflowEngine runs async DAGs without regression
- Sync wrapper works for existing callers
- shared/contracts.py has Protocol types for DNAProvider, SlopScorer, MemoryStore, Middleware
- Baseline metrics dashboard shows LLM calls/latency/errors/cost per daemon
**Plans:** 2 plans
Plans:
- [x] 00B-01-PLAN.md -- Async WorkflowEngine + Interface Contracts (ASYNC-01, ASYNC-02, CONTRACT-01)
- [x] 00B-02-PLAN.md -- Observability baseline (OBS-01)
**Status:** ✅ Complete (2026-03-29) — 52 tests passing, 4/4 must-haves verified

## Phase 1: Agent DNA
**Goal:** Universal engineering principles injected into every daemon's LLM calls via opt-in system parameter.
**Requirements:** DNA-01, DNA-02, DNA-03, DNA-04, DNA-05
**Feature flag:** ENABLE_DNA_PROFILES
**Dependencies:** Phase 0a (signed manifests), Phase 0b (contracts)
**Success criteria:**
- All 5 daemons include DNA in LLM calls when flag enabled
- DNA profiles validated against contract interface
- Circuit breaker disables DNA if error rate exceeds baseline + 10%
- Behavioral boundary test: Titan with DNA refuses wallet modification prompt
Plans:
- [x] 01-01-PLAN.md -- DNA documents + loader + circuit breaker (DNA-01, DNA-02, DNA-03, DNA-04)
- [x] 01-02-PLAN.md -- Inject DNA into all 5 daemons (DNA-05)
**Status:** ✅ Complete (2026-03-29) — 88 cumulative tests, 5/5 must-haves verified

## Phase 2: Anti-Slop Quality Gate
**Goal:** Every outbound text (email, site copy, alert) scored for quality before dispatch. Slop detected and rewritten.
**Requirements:** SLOP-01, SLOP-02, SLOP-03, SLOP-04, SLOP-05, SLOP-06, SLOP-07, SLOP-08
**Feature flag:** ENABLE_ANTI_SLOP
**Dependencies:** Phase 0b (contracts)
**Success criteria:**
- 80%+ slop detection rate on 20-sample test corpus
- Best-of-N returns highest-scoring version (not latest)
- Secret detection catches all 25+ regex patterns
- Cost per 1000 emails scored under $2 (Haiku)
- quality_scores table populated with per-content scores
Plans:
- [x] 02-01-PLAN.md -- Anti-Slop Scorer + Rewrite Loop + Secrets + DB (SLOP-01, SLOP-03, SLOP-04, SLOP-06, SLOP-07, SLOP-08)
- [ ] 02-02-PLAN.md -- Pipeline integration (SLOP-02, SLOP-05)
**Status:** In progress (Plan 01 complete)

## Phase 3: DeerFlow Persistent Memory
**Goal:** Daemons remember context across restarts. Three-tier memory with garbage collection.
**Requirements:** MEM-01, MEM-02, MEM-03, MEM-04, MEM-05, MEM-06, MEM-07, MEM-08
**Feature flag:** ENABLE_DEERFLOW_MEMORY
**Dependencies:** Phase 0b (contracts), Phase 1 (DNA for memory isolation)
**Success criteria:**
- Daemon restart preserves recent decisions and context
- Cross-daemon memory isolation: Titan cannot read ClawdBot memories
- 30-day expiry cleanup runs daily without errors
- JSON cache startup time < 500ms per daemon
- daemon_memory_stats view shows per-daemon row counts
**Status:** Not started

## Buffer Week 1: Integration Checkpoint
**Goal:** Fix any cross-phase integration issues from Phases 0-3 before building on them.
**Dependencies:** Phases 0a, 0b, 1, 2, 3

## Phase 4: DeerFlow Async Middleware Chain
**Goal:** Cross-cutting middleware pipeline for all Titan stages. Memory, DNA, anti-slop, and telemetry as composable layers.
**Requirements:** MW-01, MW-02, MW-03, MW-04, MW-05, MW-06, MW-07, MW-08
**Feature flag:** ENABLE_MIDDLEWARE
**Dependencies:** Phase 0b (async engine), Phase 1 (DNA), Phase 2 (anti-slop), Phase 3 (memory)
**Success criteria:**
- Middleware chain executes on every Titan pipeline stage
- Middleware ordering configurable (budget -> DNA -> anti-slop -> memory -> telemetry)
- stage_metrics table populated with timing and cost data
- No pipeline regression (all existing tests pass)
- Middleware stack adds < 200ms per stage
**Status:** Not started

## Phase 5: RLM Recursive Context Retrieval
**Goal:** Email composition uses recursive context expansion to reference actual details from research (review quotes, pricing gaps, competitor names).
**Requirements:** RLM-01, RLM-02, RLM-03, RLM-04, RLM-05, RLM-06, RLM-07
**Feature flag:** RLM_ENABLED
**Dependencies:** Phase 2 (anti-slop scorer as evaluator), Phase 4 (middleware for telemetry)
**Success criteria:**
- RLM emails score +15% higher specificity than single-pass (A/B test)
- No email exceeds $0.08 budget cap
- Monthly RLM spend stays under $100 cap
- Shadow mode captures comparison data for 1 week
- Feature flag toggles cleanly between RLM and original compose
**Status:** Not started

## Phase 6: Post-Quantum Cryptography
**Goal:** Conway wallet keys and API secrets protected against harvest-now-decrypt-later quantum attacks with hybrid encryption.
**Requirements:** PQC-01, PQC-02, PQC-03, PQC-04, PQC-05, PQC-06, PQC-07, PQC-08
**Feature flag:** PQC_ENABLED
**Dependencies:** Phase 0a (Conway P0 bugs fixed)
**Success criteria:**
- ARM64 validation spike passes (liboqs-python or pqcrypto works on M4)
- Wallet key encrypt/decrypt round-trip successful
- Transaction signatures verify with ML-DSA-65
- Dual-key period: both legacy and PQ keys work simultaneously
- Key rotation produces new PQ keys without losing funds
- .env secrets encrypted at rest via SecureConfig
**Status:** Not started

## Phase 7: Adaptive Thresholds (Self-Modifying Expansion)
**Goal:** Titan's expansion engine uses learnable thresholds that evolve from pipeline outcomes instead of hardcoded values.
**Requirements:** ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04, ADAPT-05, ADAPT-06, ADAPT-07, ADAPT-08
**Feature flag:** ENABLE_BANDIT_EXPANSION
**Dependencies:** Phase 4 (middleware telemetry for training data), Phase 2 (quality scores for evaluation)
**Success criteria:**
- Bandit converges after 100 simulated outcomes (thresholds shift toward conversion-correlated values)
- Historical pipeline data validates adaptive > hardcoded (backtest)
- meta_evaluations table logs every criteria change with rationale
- Concurrent experiments: 2+ shadow experiments run simultaneously
- Zero HyperAgents code or artifacts in production paths (license audit)
**Status:** Not started

## Phase 8: TRIBE v2 Neuro-Scorer
**Goal:** Predict neural activation across 4 cognitive dimensions for every outbound email/site/message. Gate low-scoring drafts. Build closed-loop learning correlating brain predictions with conversion outcomes.
**Requirements:** NEURO-01, NEURO-02, NEURO-03, NEURO-04, NEURO-05, NEURO-06, NEURO-07, NEURO-08, NEURO-09, NEURO-10
**Feature flag:** ENABLE_NEURO_SCORER
**Dependencies:** Phase 2 (anti-slop infrastructure), Phase 4 (middleware chain)
**Soft dependencies:** Phase 7 (adaptive thresholds manage neural score gates)
**License:** CC-BY-NC-4.0 (internal tooling use -- not selling predictions)
**Success criteria:**
- MPS validation spike: TRIBE v2 inference < 8s per email on M4 32GB
- 4 cognitive dimensions scored (self-relevance, trust, cognitive ease, emotional resonance)
- Lazy model loading: TRIBE v2 unloaded when not scoring, loaded on demand
- ROI extraction maps fsaverage5 atlas regions to 4 dimensions
- Neural gating: low-scoring emails re-drafted with dimension-specific guidance
- Closed-loop: dimension-outcome correlations computed daily after 50+ scored emails
- Neural rules auto-extracted when significance reached (p < 0.05, n >= 30)
- Segment-specific neural profiles computed after 100+ emails per segment
- All content types scored: emails, site copy, follow-ups, alerts
**Status:** Not started

## Buffer Week 2: Final Stabilization
**Goal:** End-to-end integration testing with all features enabled. Fix any remaining issues.
**Dependencies:** All phases

---

## Phase Dependency Graph

```
Phase 0a ──→ Phase 1 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5
          ╲              ╲                     ╱
           → Phase 0b ──→ Phase 2 ────────────╱
          ╲
           → Phase 6 (independent after 0a)

Phase 4 ──→ Phase 7 (needs telemetry data)
Phase 2 + Phase 4 ──→ Phase 8 (neuro-scorer)
Phase 7 ──→ Phase 8 (soft: bandit manages neuro gates)
```

## Feature Flags Summary

| Flag | Phase | Default | What It Controls |
|------|-------|---------|-----------------|
| ENABLE_DNA_PROFILES | 1 | false | DNA injection into LLM calls |
| ENABLE_ANTI_SLOP | 2 | false | Quality scoring on outbound content |
| ENABLE_DEERFLOW_MEMORY | 3 | false | Cross-restart daemon memory |
| ENABLE_MIDDLEWARE | 4 | false | Async middleware chain in pipeline |
| RLM_ENABLED | 5 | false | Recursive context in email compose |
| PQC_ENABLED | 6 | false | Post-quantum wallet encryption |
| ENABLE_BANDIT_EXPANSION | 7 | false | Adaptive expansion thresholds |
| ENABLE_NEURO_SCORER | 8 | false | TRIBE v2 brain activation scoring |

---
*Roadmap created: 2026-03-29 via mega-plan pipeline (full scope, Approach A with Beta corrections)*
