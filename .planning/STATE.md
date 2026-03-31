---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
last_updated: "2026-03-31T08:06:24.027Z"
progress:
  total_phases: 27
  completed_phases: 9
  total_plans: 19
  completed_plans: 23
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-29)
**Core value:** Every email reads like a human who actually looked at the business
**Current focus:** Phase 15 — Architecture (Additive)

## Current Phase

**Phase:** 14
**Status:** Phase 14 Complete
**Next action:** Phase 15 — Architecture (Additive)

## Milestone: Intel Integration (Full Scope)

- 8 work phases + 2 buffer weeks
- 56+ requirements across 9 categories
- All 7 intel references integrated (nothing deferred)
- Feature flags for every phase
- 9 detailed PLAN.md files (2,479 lines total)
- 8 new DB migrations (017-024)

## Phase Progress

| Phase | Name | Status | Plan | Quality Score |
|-------|------|--------|------|--------------|
| 0a | P0 Fixes + Skill Hardening | ✅ Complete | phases/00A-p0-bug-fixes-skill-hardening/ | 9.2 |
| 0b | Async + Contracts + Observability | ✅ Complete | phases/00B-async-engine-interface-contracts-observability/ | 9.2 |
| 1 | Agent DNA | ✅ Complete | phases/01-agent-dna/ | 9.0 |
| 2 | Anti-Slop Quality Gate | Complete | phases/02-anti-slop-quality-gate/ | 9.2 |
| 3 | DeerFlow Persistent Memory | Complete | phases/03-deerflow-persistent-memory/ | 9.0 |
| — | Buffer Week 1 | — | Integration checkpoint | — |
| 4 | DeerFlow Middleware Chain | Complete | phases/4/PLAN.md | 9.2 |
| 5 | RLM Recursive Context | Complete (2/2 plans) | phases/5/PLAN.md | 9.0 |
| 6 | Post-Quantum Crypto | Complete (2/2 plans) | phases/06-post-quantum-crypto/ | 9.0 |
| 7 | Adaptive Thresholds | Complete (3/3 plans) | phases/7/PLAN.md | 9.4 |
| 8 | TRIBE v2 Neuro-Scorer | Complete (2/2 plans) | phases/8/PLAN.md | 9.0 |
| — | Buffer Week 2 | — | Final stabilization | — |

## Migration Sequence

| Number | Phase | Table/Change |
|--------|-------|-------------|
| 017 | 0b | llm_metrics (observability) |
| 018 | 2 | quality_scores (anti-slop) |
| 019 | 3 | daemon_memory + stats view |
| 020 | 4 | stage_metrics (middleware telemetry) |
| 021 | 5 | leads.mem0_context_id (RLM) |
| 022 | 6 | encrypted_keys (PQC dual-key) |
| 023 | 7 | adaptive_thresholds + meta_evaluations |
| 024 | 8 | email_sequences.neuro_scores JSONB |

## Planning Artifacts

- `.planning/INTEL-INTEGRATION-PLANNING.md` — SEED ideation output
- `.planning/codebase/` — 8 codebase analysis documents (2579 lines)
- `.planning/discovery/` — 3 discovery reports + debate synthesis + 3 approach evaluations
- `.planning/PROJECT.md` — Full scope project definition
- `.planning/REQUIREMENTS.md` — 56+ requirements with traceability
- `.planning/ROADMAP.md` — 8 phases + dependency graph + feature flags
- `.planning/phases/0a-7/PLAN.md` — 9 detailed phase plans (2,479 lines)

## Key Constraints

- CARL Rule 2: Never mark complete without validation
- Feature flags for ALL phases (instant rollback)
- No HyperAgents code in production (CC BY-NC-SA)
- Budget: $800/month, integration adds $85-170/month

## Mega-Plan Pipeline Status

| Stage | Status | Output |
|-------|--------|--------|
| -1: SEED Ideation | Complete | INTEL-INTEGRATION-PLANNING.md |
| 0: Memory Scorer | Complete | 14 memories scored, 10 loaded |
| 0.5: CARL Rules | Complete | 3 GLOBAL rules applied |
| 1: Map Codebase | Complete | 8 documents, 2579 lines |
| 2: Discovery (x3) | Complete | 3 reports, 1223 lines |
| 3: Alpha-Beta Debate | Complete | 3 critical, 5 major corrections |
| 4: Think-at-N | Complete | Approach A selected (full scope) |
| 5: GSD Init | Complete | PROJECT.md, REQUIREMENTS.md, ROADMAP.md |
| 6: Phase Planning | Complete | 9 PLAN.md files, all quality-gated |
| 7: Final Quality Gate | Complete | Score: 9.3/10 |
| 8: Compress to Memory | Pending | — |

## Decisions

- **02-01:** Slop score uses density normalization (matches per 100 words / 10) for fair short/long content handling
- **02-01:** LLM scoring defaults to 0.5 on failure (fail-open for non-blocking quality gate)
- **02-01:** All anti-slop code in single module (shared/anti_slop.py) for cohesion
- **02-02:** Secret detection is a hard block (never sends content with secrets)
- **02-02:** Site copy gate scores each text section individually, records aggregate
- **02-02:** Rewrite loop runs on body only for emails (subject is short)
- **00B-01:** Keep sync run() unchanged, add run_async() as new method for zero backward-compat risk
- **00B-01:** Use inspect.isawaitable() for transparent sync/async system.ask() support
- **00B-01:** All 7 Protocol contracts are @runtime_checkable for startup validation
- **01-01:** Combined AgentDNA loader and DNACircuitBreaker in single module for cohesion
- **01-01:** Injection scanner fails open in dev (Rust backend optional), logs warning
- **01-01:** Circuit breaker needs minimum 10 samples before tripping (cold-start protection)
- **01-02:** Conway has no LLM calls -- no DNA injection needed
- **01-02:** Only main reasoning calls get DNA, not parsing/extraction calls
- **01-02:** _inject_dna is static method for testability without network
- **03-01:** WorkingMemory uses OrderedDict for true LRU eviction (not plain dict)
- **03-01:** MAGMA compression falls back to simple JSON merge when import fails
- **03-01:** IsolatedMemoryStore is a separate wrapper class (not mixed into DaemonMemoryStore)
- **03-01:** Memory domain cache avoids repeated YAML file reads on every access
- **03-02:** _load_memory uses cache-first startup with background Postgres reconciliation
- **03-02:** _save_memory persists all WorkingMemory entries as episodic (survives restarts)
- **03-02:** Memory init failure is non-fatal -- daemon operates without memory
- **03-02:** Cleanup job runs as non-skippable daily schedule in Perseus
- [Phase 04]: All 5 middlewares in single module for cohesion
- [Phase 04]: Budget check uses 720h window for monthly cap
- [Phase 04]: Middleware failures non-fatal except secret detection (hard block)
- **04-02:** Engine middleware gated by ENABLE_MIDDLEWARE env var (zero change when off)
- **04-02:** Async middleware chain bridged to sync engine via asyncio.run() with event-loop detection
- **04-02:** Fallback to direct execution if middleware import or chain fails
- **05-01:** All RLM code in single module (rlm_composer.py) for cohesion
- **05-01:** Mem0/Qdrant are optional imports with graceful degradation
- **05-01:** Budget check uses 720h window for monthly cap (same as Phase 4)
- **05-01:** Returns highest-scoring version across iterations, not latest
- **05-01:** Context expansion targets weakest non-slop dimension
- **05-02:** system_config DB takes precedence over env var for RLM feature flags (enables dashboard control)
- **05-02:** Shadow mode stores original email (safe), logs RLM comparison in events table
- **05-02:** RLM failure or budget exceeded falls back to original compose path
- **05-02:** A/B comparison data stored as rlm_ab_comparison events for dashboard analysis
- **06-01:** PQC libs unavailable on ARM64; proceed with cryptography library fallback (AES-256-GCM + Ed25519)
- **06-01:** Code structured for drop-in PQC upgrade when ARM64 wheels ship
- **06-01:** Bootstrap keys (CONWAY_KEYSTORE_PASSWORD, PQC_ENABLED) stored plaintext in SecureConfig
- **06-01:** Combined PQCCryptoProvider satisfies CryptoProvider Protocol from shared/contracts.py
- **06-02:** PQC signer uses HKDF-derived agent key as Ed25519 seed (deterministic per agent)
- **06-02:** PQC keystore wrapping uses pqc_wrapped:true marker for transparent classical/PQC detection
- **06-02:** PQC tx signatures are non-fatal audit trail (warning on failure, never blocks send)
- **07-01:** scipy optional -- confidence_interval returns None when unavailable (numpy suffices for sampling)
- **07-01:** ExperimentManager stores experiment data in meta_evaluations table (single audit surface)
- **07-01:** Training signal uses clients table status transitions (binary: converted vs lost/stale)
- **07-01:** Warm-start Beta(10,2): ~50 observations to wash out prior (avoids cold-start randomness)
- **07-02:** get_threshold returns natural-range values via _default_for fallback (no scaling at call site)
- **07-02:** License audit excludes compliance comments like "zero HyperAgents code"
- **07-02:** Feature flag import deferred inside if-block to avoid loading adaptive code when off
- **07-02:** Added 4th license audit check for HyperAgents-specific identifiers
- **07-03:** Backtest uses fresh in-memory bandits (no DB) for pure replay comparison
- **07-03:** Hardcoded path normalises defaults by _SCALE to get [0,1] for fair comparison

- **08-01:** TRIBE v2 not installed -- all code uses Haiku-based text-analysis fallback with keyword heuristics
- **08-01:** scipy optional -- numpy-only fallback for percentileofscore and pearsonr
- **08-01:** Synthetic atlas maps 4 dimension columns for fallback activation arrays
- **08-01:** Shared RunningNormalizer instance across NeuroScorer calls for running percentile
- **08-01:** NaN guard in pearsonr fallback for constant-value dimensions
- **08-02:** First 100 scored emails are log-only (no gating) for normalizer baseline warmup
- **08-02:** Max 2 re-draft attempts per email, keeps best-scoring version
- **08-02:** neuro_scorer_middleware placed after anti_slop in chain ordering
- **08-02:** Neural reflection and segment profiles are non-skippable scheduler jobs

- **10-01:** Direct REST calls to magic.21st.dev instead of MCP protocol (simpler, mirrors recraft_client pattern)
- **10-01:** Max 2 component fetches per curated source to stay within rate limits
- **10-01:** Component code framed as structural inspiration with React/TSX adaptation rule
- **10-01:** _fetch_component thin wrapper enables clean test mocking without httpx internals

- **10-02:** Async _resolve_design_sources_async wraps enriched call with sync fallback for resilience
- **10-02:** Component snippets extracted from design_sources in build_plan, formatted as COMPONENT PATTERNS block
- **10-02:** design_sources key added to _resolve_build_plan return dict to carry snippets through pipeline
- **10-02:** Component blocks truncated to 1200 chars each to prevent prompt bloat

- **12-01:** Patch shared.agent_base.db in tests for cross-module DB mock isolation (conftest swaps shared.db)
- **12-01:** f-string SQL for lock_clause uses hardcoded constants only (not user input)
- **12-01:** forbidden_token middleware delegates redaction to existing credential_stripper
- **12-01:** Session health flush in _save_memory ensures final snapshot persisted at deregistration

- **11-01:** ENABLE_CONSOLIDATED_BUDGET uses os.environ.get (not _flag) because _flag defaults true for security
- **11-01:** check_budget_for_llm_call hardcoded ON after legacy removal (two-commit cutover)
- **11-01:** Budget cap reads from config.budget.monthly_cap via _get_budget_cap() with $800 fallback
- **11-01:** _budget_gate fully removed in Commit 2 (Commit 1 is safe rollback point)
- [Phase 13]: emit_event uses shared.db.emit_event (not shared.comms)
- [Phase 13]: Budget middleware fails CLOSED on DB errors (AEGIS fix from fail-open)
- [Phase 13]: All 3 feature flags default OFF for 48hr shadow mode
- [Phase 14]: Behavioral evals adapted to actual codebase APIs (middleware, agent_state, compliance)
- [Phase 14]: Heartbeat uses existing session_health table schema with metrics JSONB for type differentiation
- [Phase 14]: Credential stripper expanded to 16 patterns (was 6) per AEGIS audit requirement

## Performance Metrics

| Phase-Plan | Duration | Tasks | Files |
|-----------|----------|-------|-------|
| 00B-01 | 3min | 3 | 3 |
| 01-01 | 7min | 4 | 10 |
| 01-02 | 5min | 2 | 10 |
| 02-01 | 12min | 5 | 3 |
| 02-02 | 8min | 3 | 5 |
| 03-01 | 3min | 5 | 2 |
| 03-02 | 4min | 3 | 4 |
| 04-01 | 3min | 7 | 2 |
| 04-02 | 8min | 2 | 3 |
| 05-01 | 2min | 5 | 2 |
| 05-02 | 5min | 3 | 2 |
| 06-01 | 5min | 6 | 4 |
| 06-02 | 5min | 2 | 6 |
| 07-01 | 6min | 6 | 4 |
| 07-02 | 4min | 3 | 3 |
| 08-01 | 7min | 6 | 8 |
| 07-03 | 2min | 2 | 2 |
| 08-02 | 8min | 4 | 5 |
| 10-01 | 2min | 2 | 4 |
| 10-02 | 5min | 2 | 4 |
| 11-01 | 8min | 13 | 7 |
| 12-01 | 22min | 6 | 15 |
| 13-01 | 7min | 6 | 11 |
| 14-01 | 29min | 13 | 23 |

---
*State updated: 2026-03-31 -- Phase 14 complete (quality & observability: migration 027, 6 behavioral eval suites (28 tests), HeartbeatEmitter + 5 lifecycle docs, RedactingFormatter + 16 credential patterns, log redaction wired -- 45 new tests, 3 feature flags)*
*State updated: 2026-03-31 -- Phase 13 complete (budget & cost patterns: migration 026, CostEvent + emit, multi-scope policies, pre-execution budget gate, AEGIS fail-closed fix, cost breakdown API -- 37 tests, 3 feature flags)*
*State updated: 2026-03-31 -- Phase 12 complete (foundation patterns: agent state machine, atomic checkout, recursion guard, forbidden token scanner, session health -- 30 tests, 5 feature flags)*
*State updated: 2026-03-31 -- Phase 11 complete (budget consolidation: check_budget_for_llm_call single authority, fail-closed, legacy _budget_gate removed, 71 tests)*
*State updated: 2026-03-30 -- Phase 10 Plan 02 complete (pipeline integration: enriched components in variant prompts, Recraft budget tests, quality comparison, 29 tests)*
*State updated: 2026-03-30 -- Phase 10 Plan 01 complete (21st.dev REST client + design_sources.py async enrichment, 15 tests)*
*State updated: 2026-03-30 -- Phase 08 Plan 02 complete (pipeline integration: neural gating in email compose, neuro_scorer_middleware, scheduler jobs, 58 tests)*
*State updated: 2026-03-30 -- Phase 08 Plan 01 complete (neuro-scorer core: TribeService, ROIExtractor, RunningNormalizer, NeuroScorer, learning loop, migration 024)*
*State updated: 2026-03-30 -- Phase 07 Plan 03 complete (gap closure: backtest function, concurrent experiment tests, 58 total tests)*
*State updated: 2026-03-30 -- Phase 07 Plan 02 re-executed (53 tests, scaling fix, 4-check license audit, DB round-trip tests)*
*State updated: 2026-03-30 -- Phase 07 Plan 01 complete (Thompson sampling bandits, migration 023, 41 tests)*
*State updated: 2026-03-30 -- Phase 06 Plan 02 complete (wallet PQC integration, 73 tests, from_raw_bytes bug fix)*
*State updated: 2026-03-30 -- Phase 06 complete (PQC core, SecureConfig, migration 022, key derivation, key rotation)*
*State updated: 2026-03-30 -- Phase 05 complete (shadow A/B, system_config toggle, 26 tests)*
*State updated: 2026-03-30 -- Phase 05 Plan 01 complete (RLM composer, Mem0/Qdrant, budget caps, migration 021)*
*State updated: 2026-03-29 -- Phase 04 Plan 02 complete (engine integration + 42 tests)*
*State updated: 2026-03-29 -- Phase 03 complete (lifecycle hooks, scheduler cleanup, 41 tests)*
*State updated: 2026-03-29 -- Phase 03 Plan 01 complete (memory store, cache, migration, isolation)*
*State updated: 2026-03-29 -- Phase 02 complete (pipeline integration + 50 tests)*
*State updated: 2026-03-29 -- Phase 02 Plan 01 complete (anti-slop scorer, rewrite loop, secrets, DB migration)*
*State updated: 2026-03-29 -- Phase 01 complete (DNA injection wired into all daemons, 36 tests passing)*
| Phase 14 P01 | 29min | 13 tasks | 23 files |
