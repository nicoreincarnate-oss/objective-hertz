# Intel Integration — Full Scope

## What This Is

Full integration of 7 research references into Objective Hertz's 5-daemon autonomous revenue system. 8 phases covering: universal engineering DNA, anti-slop quality gates, persistent daemon memory, async middleware chain, recursive language model context retrieval, post-quantum cryptography for wallet/secrets, and adaptive self-modifying expansion thresholds. Nothing deferred. Everything integrated.

## Core Value

Every email Titan sends reads like a human who actually looked at the business — specific, natural, and backed by full research context. And the system that produces those emails continuously improves how it judges quality, remembers what works, and protects its assets.

## Requirements

### Validated

- ✓ 10-stage revenue pipeline (discover through invoice) — existing
- ✓ 5-daemon architecture with A2A communication — existing
- ✓ Email composition with content validation (fabricated claims, spam triggers) — existing
- ✓ Budget enforcement with auto-downgrade to Ollama — existing
- ✓ CAN-SPAM compliance with immutable audit log — existing
- ✓ War Room dashboard with operator interface — existing
- ✓ Conway wallet infrastructure (Base L2 USDC) — existing (has known bugs)

### Active

**Phase 0a: P0 Bug Fixes + Skill Hardening**
- [ ] **FIX-01**: Conway wallet INSERT column order fixed (agent_name, chain correct)
- [ ] **FIX-02**: Conway keystore uses env-var password, not agent name
- [ ] **FIX-03**: Conway survival tier JSONB writes use proper wrapper
- [ ] **FIX-04**: Skill loader uses signed manifests (operator-signed, verified at load time)

**Phase 0b: Async Engine + Interface Contracts**
- [ ] **ASYNC-01**: WorkflowEngine.run() converted to async with sync wrapper
- [ ] **ASYNC-02**: All call sites of WorkflowEngine.run() audited and migrated
- [ ] **CONTRACT-01**: shared/contracts.py with Protocol types for DNAProvider, SlopScorer, MemoryStore, Middleware
- [ ] **OBS-01**: Observability baseline (LLM call counts, latency, error rates, cost per daemon)

**Phase 1: Agent DNA**
- [ ] **DNA-01**: soul/engineering_dna.md with universal principles (security, compliance, budget, quality, resilience, decisions, learning)
- [ ] **DNA-02**: Per-daemon DNA profiles (soul/dna/*.yaml) with role boundaries, permitted tools, escalation triggers
- [ ] **DNA-03**: shared/agent_dna.py loader with opt-in injection (use_dna=True), 500-token cap, injection scanner
- [ ] **DNA-04**: Circuit breaker disables DNA if error rates exceed baseline
- [ ] **DNA-05**: All 5 daemons pass DNA through LLM calls

**Phase 2: Anti-Slop Quality Gate**
- [ ] **SLOP-01**: shared/anti_slop.py with 5-dimension scorer (clarity, specificity, authenticity, value-density, slop-score) using Haiku
- [ ] **SLOP-02**: Quality gate between email_compose and email_send in Titan pipeline
- [ ] **SLOP-03**: Best-of-N rewrite loop — tracks scores across iterations, returns highest-scoring version
- [ ] **SLOP-04**: "Good enough" threshold skips unnecessary rewrite iterations
- [ ] **SLOP-05**: Quality gate on ClawdBot site copy before Netlify deploy
- [ ] **SLOP-06**: Secret detection regex (25+ patterns) on all outgoing content
- [ ] **SLOP-07**: Standalone quality_scores table with trend analysis capability
- [ ] **SLOP-08**: Configurable thresholds per context (email=strict, alert=relaxed, site=strict)

**Phase 3: DeerFlow Persistent Memory**
- [ ] **MEM-01**: daemon_memory Postgres table with expires_at (30-day default), per-daemon row cap (10K)
- [ ] **MEM-02**: Working memory in-process only (Python dict), NOT Postgres
- [ ] **MEM-03**: Episodic memory with daily cleanup job via Perseus scheduler
- [ ] **MEM-04**: Semantic memory persists indefinitely with MAGMA compression when cap exceeded
- [ ] **MEM-05**: JSON cache write-through for fast daemon startup
- [ ] **MEM-06**: Memory injection into daemon startup via shared/agent_base.py lifecycle hooks (_load_memory/_save_memory)
- [ ] **MEM-07**: Per-daemon memory isolation enforced by DNA profiles (memory_domains field)
- [ ] **MEM-08**: daemon_memory_stats view for Hermes monitoring

**Phase 4: DeerFlow Async Middleware Chain**
- [ ] **MW-01**: Async middleware protocol: async def middleware(ctx, next) -> StageResult
- [ ] **MW-02**: Middleware stack configurable per pipeline
- [ ] **MW-03**: MemoryMiddleware — reads relevant memories before stage, writes outcome after
- [ ] **MW-04**: DNAGuardMiddleware — validates stage actions against agent DNA boundaries
- [ ] **MW-05**: AntiSlopMiddleware — wraps content-producing stages with quality scoring
- [ ] **MW-06**: TelemetryMiddleware — logs stage timing, token usage, cost to stage_metrics table
- [ ] **MW-07**: stage_metrics Postgres table for telemetry data
- [ ] **MW-08**: Middleware ordering configurable (budget check before anti-slop)

**Phase 5: RLM Recursive Context Retrieval**
- [ ] **RLM-01**: titan/pipeline/rlm_composer.py with recursive compose loop (draft → evaluate → refine, max 3 iterations)
- [ ] **RLM-02**: Mem0 write path — store full research output with mem0_context_id on leads table
- [ ] **RLM-03**: Qdrant read path — fast vector retrieval during email composition
- [ ] **RLM-04**: $0.08/email budget cap with $100/month global RLM spend cap
- [ ] **RLM-05**: Haiku for evaluation step (reuses Anti-Slop scorer from Phase 2)
- [ ] **RLM-06**: Shadow mode A/B testing (RLM vs original compose) for 1 week before cutover
- [ ] **RLM-07**: Feature flag RLM_ENABLED with instant rollback to single-pass

**Phase 6: Post-Quantum Cryptography**
- [ ] **PQC-01**: 2-day ARM64 validation spike (liboqs-python on M4, fallback to pqcrypto)
- [ ] **PQC-02**: conway/pqc.py with hybrid ML-KEM-768 + AES-256 for key encapsulation
- [ ] **PQC-03**: ML-DSA-65 signatures on Conway wallet transactions
- [ ] **PQC-04**: SecureConfig class for .env secret encryption at rest
- [ ] **PQC-05**: encrypted_keys table with dual-key support (legacy + PQ valid for 30 days)
- [ ] **PQC-06**: Per-agent key derivation (replaces single shared CONWAY_KEYSTORE_PASSWORD)
- [ ] **PQC-07**: Key rotation mechanism for Conway wallets
- [ ] **PQC-08**: Feature flag PQC_ENABLED with rollback to classical crypto

**Phase 7: Adaptive Thresholds (Self-Modifying Expansion)**
- [ ] **ADAPT-01**: titan/adaptive_thresholds.py with Thompson sampling bandit from textbook sources (Sutton & Barto)
- [ ] **ADAPT-02**: DB-stored expansion thresholds replacing 5 hardcoded values in expansion.py
- [ ] **ADAPT-03**: Training signal from pipeline outcome data (lead conversion binary)
- [ ] **ADAPT-04**: Warm-start with priors from current hardcoded thresholds (Beta(10,2) distribution)
- [ ] **ADAPT-05**: Concurrent A/B experiments (multiple shadow experiments, not just one)
- [ ] **ADAPT-06**: meta_evaluations table tracking criteria evolution and outcomes
- [ ] **ADAPT-07**: Feature flag ENABLE_BANDIT_EXPANSION
- [ ] **ADAPT-08**: Zero HyperAgents code in production (CC BY-NC-SA compliance)

### Out of Scope

- HyperAgents codebase — CC BY-NC-SA 4.0 license prohibits commercial use. Clean-room Thompson sampling implementation only.
- Quantum computing (QAOA) — Trading project only, not OH.
- Full agent spawning (cell_division.py) — AgentSpawnTool is hollow, separate effort.
- On-chain agent registry (ERC-8004) — Address is all zeros, separate effort.

## Context

**Research pipeline:** 7 intel reference documents synthesized through mega-plan pipeline: SEED ideation (10 sections), memory scoring (14 memories, 10 loaded), CARL rules (3 GLOBAL), codebase mapping (4 agents, 8 documents, 2579 lines), parallel discovery (3 agents, 1223 lines), alpha-beta debate (3 critical + 5 major corrections), think-at-n (3 approaches evaluated).

**Codebase state:** Pipeline wired end-to-end (579 tests). First-sale capable. Known bugs: Conway wallet P0s (3), orchestrator no A2A server, sync pipeline, 20+ silent exception swallowing locations.

**Key code surfaces:**
- `shared/llm_client.py` — DNA injection (system parameter in generate())
- `titan/pipeline/email_compose.py` lines 193-198 — Anti-slop insertion after LLM generation
- `titan/pipeline/email_send.py` lines 84-134 — Micro-review personas (anti-slop augments)
- `titan/expansion.py` lines 44-92 — 5 hardcoded thresholds (adaptive replacement target)
- `conway/wallet.py` lines 227-278 — Keystore management (PQC target)
- `shared/agent_base.py` — DeerFlow memory lifecycle hooks
- `openjarvis/workflow/engine.py` line 39 — Sync run() (async fix target)
- `shared/skill_loader.py` lines 30-56 — Untrusted skill loading (hardening target)

## Constraints

- **Budget**: $800/month total. Full integration adds $85-170/month.
- **Compatibility**: All changes backward-compatible via feature flags (one per phase).
- **License**: Zero HyperAgents code in production. Thompson sampling from Sutton & Barto only.
- **Testing**: Every phase validated with ruff + pytest (CARL Rule 2).
- **Parallelism**: Independent tool calls batched in parallel (CARL Rule 1).
- **ARM64**: PQC Phase 6 requires liboqs-python validation spike on M4 before full implementation.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Full Approach A (8 phases, all references) | User explicitly wants everything integrated. Time is never a constraint. | — Pending |
| Beta's corrections applied to Alpha's proposal | 3 critical, 5 major, 4 minor corrections improve safety | — Pending |
| Standalone quality_scores table | Enables trend analysis, A/B testing | — Pending |
| Mem0 writes, Qdrant reads for RLM | Clean write-path / read-path separation | — Pending |
| DNA injection opt-in per call (use_dna=True) | Prevents fragility from global injection | — Pending |
| Feature flags for ALL phases | Enables instant rollback without code reverts | — Pending |
| No HyperAgents code in production | CC BY-NC-SA prohibits commercial use | — Pending |
| Signed manifests for skill loader | Prevents tampered DNA/skill injection | — Pending |
| Interface contracts in Phase 0b | Prevents Phase 4 integration cliff | — Pending |
| Working memory in-process only | Postgres is overkill for session-scoped data | — Pending |
| 30-day episodic memory expiry with GC | Prevents unbounded table growth | — Pending |
| Haiku for anti-slop scoring + RLM evaluation | Cost-efficient, reuses same scorer component | — Pending |
| Warm-start bandit with current threshold priors | Avoids weeks of random decisions during cold-start | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-29 after mega-plan initialization (full scope)*
