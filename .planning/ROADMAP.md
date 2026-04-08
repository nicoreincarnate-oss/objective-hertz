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
- [x] 02-02-PLAN.md -- Pipeline integration (SLOP-02, SLOP-05)
**Status:** ✅ Complete (2026-03-29) — 138 cumulative tests, 100% slop detection, 5/5 verified

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
Plans:
- [x] 03-01-PLAN.md -- Memory Store + Cache + Migration (MEM-01, MEM-02, MEM-03, MEM-04, MEM-05, MEM-07)
- [x] 03-02-PLAN.md -- Lifecycle hooks + tests (MEM-06, MEM-08)
**Status:** ✅ Complete (2026-03-29) — 179 cumulative tests, 5/5 verified

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
**Status:** ✅ Complete (2026-03-29) — 221 cumulative tests, 5/5 verified

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
**Status:** ✅ Complete (2026-03-29) — 247 cumulative tests, 5/5 verified

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
Plans:
- [x] 06-01-PLAN.md -- PQC Core + SecureConfig + Migration (PQC-01, PQC-02, PQC-03, PQC-04, PQC-05, PQC-06, PQC-07)
- [x] 06-02-PLAN.md -- Feature Flag + Tests (PQC-08) -- 73 tests, wallet PQC integration, from_raw_bytes bug fix
**Status:** ✅ Complete (2026-03-30) -- 2/2 plans, 73 tests, wallet PQC integration verified

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
Plans:
- [x] 07-01-PLAN.md -- Thompson sampling bandits, DB persistence, training signal, experiments, migration 023 (ADAPT-01 through ADAPT-06)
- [x] 07-02-PLAN.md -- Expansion wiring + License audit + Tests (ADAPT-07, ADAPT-08) -- 53 tests, 4-check license audit, DB round-trip
- [x] 07-03-PLAN.md -- Gap closure: backtest function + concurrent experiment tests (ADAPT-05)
**Status:** Gaps found (2026-03-30) -- 3 plans (2 complete, 1 gap closure pending)

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
Plans: 2 (08-01: Core + ROI + Learning, 08-02: Pipeline Integration + Tests)
- [x] 08-01-PLAN.md -- NeuroScorer core: TribeService, ROIExtractor, RunningNormalizer, learning loop, migration 024
- [x] 08-02-PLAN.md -- Pipeline integration: neural gating, neuro_scorer_middleware, scheduler jobs, 58 tests
**Status:** ✅ Complete (2026-03-30) — 436 cumulative tests, 9/9 verified

## Phase 9: Audit Gap Closure + Integration Fixes
**Goal:** Fix all gaps identified by v1.0 milestone audit — broken test fixtures, missing logger, unwired memory cleanup handler, and checkbox drift.
**Requirements:** FIX-02, FIX-03, MW-01, MW-08, MEM-03
**Feature flag:** N/A (fixes to existing code)
**Dependencies:** Phases 0a, 0b, 3, 4 (fixes artifacts from these phases)
**Gap Closure:** Closes gaps from v1.0-MILESTONE-AUDIT.md
**Success criteria:**
- All 9 survival JSONB tests pass (fixture bug fixed)
- All 5 keystore security tests pass (mock 0x prefix fixed)
- engine.py logger defined — middleware fallback logs warning instead of crashing
- memory_cleanup handler in TASK_HANDLERS — cleanup_expired() called daily
- hermes/web/app.py F811 resolved (no duplicate function names)
- REQUIREMENTS.md checkboxes match verification status
Plans: Most gaps already resolved inline during Phases 0a-8 execution
**Status:** ✅ Resolved inline — survival JSONB (Phase 0a), keystore mock (Phase 0a), F811 (Phase 0b), logger (Phase 4 middleware fallback)

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
Phase 2 + Phase 4 ──→ Phase 10 (site builder pipeline)
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

### Phase 10: 21st.dev + Recraft Pipeline Integration

**Goal:** Wire real 21st.dev component library (via REST API) and Recraft image generation into ClawdBot site builder. Replace text-only prompt references with actual component fetching, adaptation, and injection.
**Requirements**: TWENTY1-01, TWENTY1-02, TWENTY1-03, RECRAFT-01, RECRAFT-02, RECRAFT-03, PIPELINE-01, PIPELINE-02
**Feature flag:** N/A (pipeline infrastructure)
**Depends on:** Phase 2 (anti-slop), Phase 4 (middleware)
**Success criteria:**
- 21st.dev REST API configured and queryable from site builder
- Real component code injected into variant prompts (not just text references)
- Recraft generates logo + hero images with budget tracking
- Full pipeline runs: strategy -> assets -> 5 variants -> Opus review -> synthesis -> anti-slop -> deploy
- Built sites score higher on site_quality QA than text-reference-only builds
**Plans:** 5/5 plans complete

Plans:
- [x] 10-01-PLAN.md -- 21st.dev REST client + design_sources enrichment (TWENTY1-01, TWENTY1-02, TWENTY1-03)
- [x] 10-02-PLAN.md -- Pipeline wiring + Recraft verification + quality comparison (RECRAFT-01, RECRAFT-02, RECRAFT-03, PIPELINE-01, PIPELINE-02)

## Phase 11: Budget Consolidation
**Goal:** Consolidate 4 scattered budget enforcement points into a single middleware authority that fails closed on DB errors. Prerequisite for all Paperclip budget patterns.
**Requirements:** BUDGET-CONSOLIDATE-01 through BUDGET-CONSOLIDATE-07
**Feature flag:** ENABLE_CONSOLIDATED_BUDGET
**Dependencies:** Phase 4 (middleware chain), Phase 9 (AEGIS fixes)
**Success criteria:**
- Budget check lives in ONE place: shared/middleware.py:budget_check_middleware
- _budget_gate removed from shared/llm_client.py
- Budget fails CLOSED on DB error (Ollama fallback, not pass-through)
- Two-commit cutover strategy executed safely
- All existing tests pass
Plans:
- [x] 11-01-PLAN.md -- Upgrade budget_check_middleware + test suite + legacy removal
**Status:** Not started

## Phase 12: Foundation Patterns
**Goal:** Port 5 infrastructure patterns from Paperclip (MIT): agent state machine, atomic task checkout, recursion guard, forbidden token scanner, session health tracking.
**Requirements:** FP-01 through FP-05
**Feature flags:** AGENT_STATE_MACHINE_ENABLED, ATOMIC_CHECKOUT_ENABLED, RECURSION_GUARD_ENABLED, FORBIDDEN_TOKEN_SCAN_ENABLED, SESSION_HEALTH_ENABLED
**Dependencies:** Phase 11 (budget consolidation)
**Success criteria:**
- Agents have 7 lifecycle states with validated transitions
- Tasks use FOR UPDATE SKIP LOCKED for atomic checkout
- Task depth > 5 rejected by recursion guard
- Forbidden tokens scanned on LLM output with test exclusions
- Session health extends DeerFlow WorkingMemory
- Migration 025 applies cleanly
Plans:
- [x] 12-01-PLAN.md -- 5 foundation patterns + migration 025 + 30 tests
**Status:** Complete (2026-03-31) -- 30/30 tests passing, 7 commits

## Phase 13: Budget & Cost Patterns
**Goal:** Implement 3 Paperclip budget patterns: pre-execution budget gate with cost estimation, multi-scope budget policies per-agent/project, and per-call cost events with task attribution.
**Requirements:** BUDGET-01 through BUDGET-03
**Feature flags:** PRE_EXECUTION_BUDGET_GATE_ENABLED, MULTI_SCOPE_BUDGET_ENABLED, PER_CALL_COST_EVENTS_ENABLED
**Dependencies:** Phase 11 (consolidated budget), Phase 12 (agent state machines)
**Success criteria:**
- Pre-task cost estimation from historical averages (default to list prices for first 100 tasks)
- budget_policies table seeded with $800/month company default
- cost_events table records every LLM call with task attribution
- Budget fails CLOSED per AEGIS (DB errors reject, Ollama fallback)
- 48hr shadow mode before enforcement
- Migration 026 applies cleanly
Plans:
- [ ] 13-01-PLAN.md -- Budget gate + multi-scope policies + cost events + migration 026 + 30 tests
**Status:** Not started

## Phase 14: Quality & Observability
**Goal:** Implement 3 Paperclip quality patterns: pytest behavioral evals for daemon behaviors, heartbeat lifecycle protocol with per-daemon soul docs, and log redaction via credential stripper.
**Requirements:** QUAL-01 through QUAL-03
**Feature flags:** BEHAVIORAL_EVALS_ENABLED, HEARTBEAT_LIFECYCLE_ENABLED, LOG_REDACTION_ENABLED
**Dependencies:** Phase 12 (state machine for eval tests), Phase 13 (cost tracking for eval data)
**Success criteria:**
- 6 critical behavioral evals pass (budget fail-closed, CAN-SPAM, middleware wired, escalation, state machine, recursion)
- HeartbeatEmitter writes to session_health every 30s per daemon
- 5 lifecycle docs in soul/lifecycle/ (one per daemon)
- Credential stripper wired into logging handler (15+ patterns)
- Log redaction < 1ms latency per line
- Migration 027 applies cleanly
Plans:
- [x] 14-01-PLAN.md -- Behavioral evals + heartbeat + log redaction + migration 027
**Status:** Not started

## Phase 15: Architecture - Additive
**Goal:** Implement 3 additive Paperclip architecture patterns: goal cascade hierarchy, governance/approval system (closes AEGIS review_mode finding), and commit metrics tracker.
**Requirements:** ARCH-01 through ARCH-03
**Feature flags:** GOAL_CASCADE_ENABLED, GOVERNANCE_ENABLED, COMMIT_METRICS_ENABLED
**Dependencies:** Phase 14 (quality infrastructure)
**Success criteria:**
- Goals table with company/team/agent/task hierarchy, seeded with revenue goals
- task_queue.goal_tag auto-populated when cascade enabled
- Approvals table gates review_mode True→False transition (AEGIS fix)
- Commit metrics track Co-Authored-By for agent attribution
- Migration 028 applies cleanly
Plans:
- [ ] 15-01-PLAN.md -- Goal cascade + governance + commit metrics + migration 028
**Status:** Not started

## Phase 16: Event-Driven Wakeup Queue
**Goal:** Replace fixed-schedule polling with event-driven LISTEN/NOTIFY wakeup. Agents sleep until relevant events fire. Fallback polling always active as safety net.
**Requirements:** WAKEUP-01
**Feature flag:** EVENT_WAKEUP_ENABLED (3-mode: off/shadow/true)
**Dependencies:** Phase 15 (all prior patterns stable)
**Success criteria:**
- Postgres LISTEN/NOTIFY triggers on events table inserts
- Dedicated non-pooled LISTEN connection with exponential-backoff reconnect
- Wakeup subscriptions seeded for all 4 daemons
- Coalescing merges duplicate pending wakeups
- Fallback poll NEVER removed (60s timeout safety net)
- 72-96hr shadow mode before full enablement
- Migration 029 applies cleanly
Plans:
- [ ] 16-01-PLAN.md -- Wakeup queue + trigger + subscriptions + migration 029
**Status:** Not started

---
*Roadmap created: 2026-03-29 via mega-plan pipeline (full scope, Approach A with Beta corrections)*
*Phase 10 planned: 2026-03-30 — 2 plans in 2 waves*
*Phases 11-16 planned: 2026-03-30 — Paperclip infrastructure integration (mega-plan Approach B, quality 8.9/10)*

## Phase 3: Daemon Wiring (Full Implementation)
**Goal:** Wire the new Phase 42.5 v2 modules into all 8 daemons so they actually get imported + called at runtime. P0-2 ghost integration fix — the modules exist on disk but no daemon currently imports them. Close the gap between "built" and "running."
**Requirements:** P0-2 execution (full daemon wiring), metadata plumbing across 151 call sites, Ruflo Aider runtime wiring, Clawdbot Aider runtime wiring, Hermes voice loop runtime wiring, Clawdbot image gen runtime wiring
**Feature flag:** AUTO_TIER_ENABLED + ENABLE_AIDER_LOOPS + ENABLE_VOICE_LOOP + ENABLE_DRAW_THINGS (all default OFF until operator confirms post-cutover)
**Dependencies:** Phase 1 + Phase 2 complete. PORT-PLAN locked (7 decisions). Trinity swap committed. llm_client.py canonical at 1711 lines.
**Success criteria:**
- All 151 llm.generate() call sites in 8 daemons pass daemon_name + operation metadata
- auto_tier=True hook accessible across all daemons (no errors importing)
- Ruflo bug-fix path invokes run_ruflo_aider_loop with SandboxRunner default
- Clawdbot site-builder can invoke run_clawdbot_aider_loop for architect+editor workflows
- Hermes voice loop (Parakeet + Kokoro + intent_router) wired into hermes/jarvis/ (dead path until operator starts the daemons)
- Clawdbot image gen wired to draw_things_client (dead path until operator starts Draw Things)
- pytest full suite passes with zero regressions vs Phase 2 baseline (33 passed / 6 pre-existing failed)
- All existing daemon tests still pass
- Phase 3 marked complete in STATE.md

**Constraints:**
- ABSOLUTE paths everywhere
- Atomic commits per wave (1 commit per daemon for Wave 1, 1 per daemon for Waves 2-5)
- NEVER modify .env
- NEVER install brew/pip/ollama
- NEVER start daemons
- NEVER touch main repo directly
- Layer C (voice + imagegen) wires code paths but they stay DEAD until operator starts Parakeet/Kokoro/Draw Things services

**Plans:**
- [x] 03-01-PLAN.md — Metadata plumbing via migrate_to_litellm --apply across 8 daemons + spend_alerts import wiring (Layer A, low-risk)
- [x] 03-02-PLAN.md — Ruflo Aider loop runtime wiring (Layer B medium-risk)
- [x] 03-03-PLAN.md — Clawdbot Aider loop runtime wiring (Layer B medium-risk, customer-facing)
- [x] 03-04-PLAN.md — Hermes voice loop wiring (Layer C, dead-path until services alive)
- [x] 03-05-PLAN.md — Clawdbot image gen wiring (Layer C, dead-path until Draw Things alive)

**Status:** ✅ Complete (2026-04-08) — all 5 waves SHIP, 22 commits, 86 passed / 6 pre-existing failures / 18 new Phase 3 tests green, zero regressions
