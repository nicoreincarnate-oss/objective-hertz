# Technical Discovery Report: 7 Intel Integrations

**Date:** 2026-03-29
**Scope:** Architecture analysis of integrating 7 research references into Objective Hertz
**Method:** Code-level inspection of all integration surface files
**Status:** Discovery complete -- ready for planning

---

## Summary Matrix

| # | Integration | Complexity | LOC Estimate | New Deps | DB Migration | Async Concern |
|---|-------------|-----------|-------------|----------|-------------|---------------|
| 1 | RLM (Recursive Language Models) | High | 350-450 | recursive-llm, RestrictedPython | Yes (1 col) | Critical |
| 2 | HyperAgents (Meta-Evaluation) | Medium | 250-350 | None | Yes (1 table) | Low |
| 3 | Anti-Slop (Quality Gate) | Low | 150-200 | None | Yes (1 table) | None |
| 4 | DeerFlow (Persistent Memory) | Medium | 300-400 | None | Yes (1 table) | Low |
| 5 | Agent DNA | Low-Medium | 200-250 | None | No | None |
| 6 | PQC (Post-Quantum Crypto) | High | 400-500 | liboqs-python | Yes (1 table, 1 col) | Low |
| 7 | Quantum Computing | SCOPED OUT | 0 | N/A | N/A | N/A |

**Total estimated LOC:** 1,650-2,150 (excluding tests)
**Total new tables:** 4 (quality_scores, daemon_memory, meta_evaluations, encrypted_keys)
**Total modified tables:** 3 (leads, pipeline_tasks, agent_wallets)

---

## 1. RLM -- Recursive Language Models

### Integration Surface
- **Primary:** `shared/llm_client.py` (418 LOC) -- `generate()` method at line 67
- **Secondary:** `titan/memory.py` (1,878 LOC) -- `store_memory()` and `get_relevant_learnings()`
- **Infrastructure:** Mem0 service (port 8888) + Qdrant vector store

### What Changes
RLM replaces single-shot LLM calls with recursive sub-queries during email composition. When `email_compose.py` needs context about a lead, instead of pulling flat DB columns, it calls an RLM REPL that recursively retrieves and synthesizes research from Mem0/Qdrant.

### Complexity: HIGH

**Reason:** The `generate()` method in `llm_client.py` is the single chokepoint for all LLM calls across all 5 daemons. Any change here has blast radius across the entire system. Additionally, RLM introduces recursion into what is currently a flat call chain -- a new failure mode.

### Code Analysis

`llm_client.py` line 67-124: `generate()` accepts `system`, `prompt`, `model` parameters. RLM needs a new parameter (e.g., `recursive_context: bool = False`) or a separate method `generate_with_rlm()` to avoid polluting the hot path. The budget gate at line 179 will fire on each recursive sub-call, so RLM could burn through budget 3-5x faster per composition cycle.

`titan/memory.py` line 34-78: `store_memory()` writes to Mem0 via HTTP POST. RLM needs the inverse -- a `query_memory_recursive()` that makes multiple passes. The existing `get_relevant_learnings()` does single-pass vector search; RLM needs iterative refinement.

### LOC Estimate: 350-450
- `shared/rlm_client.py` (new): ~150 LOC -- RLM REPL wrapper, sandboxed execution
- `shared/llm_client.py` (modify): ~50 LOC -- add `generate_with_rlm()` method
- `titan/memory.py` (modify): ~80 LOC -- recursive query function
- `titan/pipeline/email_compose.py` (modify): ~70 LOC -- swap single-shot for RLM composition

### New Dependencies
| Package | Risk | Notes |
|---------|------|-------|
| `recursive-llm` | Medium | MIT license, LiteLLM-based. Unknown async performance under concurrent pipeline cycles. Must benchmark before integrating. |
| `RestrictedPython` | Low | Zope project, production-grade. Needed for sandboxed REPL execution. |

### Async Concern: CRITICAL
`recursive-llm` documentation shows sync-first API. The Titan pipeline stages are all `async def` (confirmed: `email_compose.py` line 40 is `async def compose_emails`). If recursive-llm blocks, it will freeze the entire Titan event loop.

**Mitigation options:**
1. Run RLM in `asyncio.to_thread()` -- adds thread overhead but preserves async pipeline
2. Wrap recursive-llm calls in a `ThreadPoolExecutor` -- already used by WorkflowEngine (line 87)
3. Fork recursive-llm and add native async support -- highest effort, best performance

### Prerequisite Fixes
1. **Budget gate awareness:** The `_budget_gate()` at line 179 will trigger per sub-query. Need to add a `budget_context` parameter that groups recursive calls under one budget allocation. Without this, a 4-step RLM chain could exhaust the alert threshold on a single lead.
2. **Mem0 error handling:** `store_memory()` at line 71 silently catches all exceptions. RLM needs the search path to be reliable -- add retry logic with exponential backoff to `get_relevant_learnings()`.

### Database Migration
- Add `mem0_context_id TEXT` column to `leads` table -- stores reference to full research context blob in Mem0 for RLM retrieval

### Testing Strategy
- Unit: Mock `recursive-llm` responses, test that `generate_with_rlm()` falls back gracefully when Mem0 is down
- Integration: End-to-end pipeline test with a real lead flowing through discovery -> research -> compose, comparing RLM output quality vs baseline
- Budget: Test that a 5-step recursive chain respects budget caps without draining monthly allocation on 3 leads
- Performance: Benchmark latency of RLM compose vs current single-shot (target: < 2x slowdown for > 3x quality)

---

## 2. HyperAgents -- Meta-Evaluation

### Integration Surface
- **Primary:** `titan/expansion.py` (578 LOC) -- `_detect_revenue_bottlenecks()` at line 40
- **Secondary:** Skill evaluation logic in expansion.py (hardcoded thresholds)
- **Infrastructure:** Docker Compose (new containers for meta-agent isolation)

### What Changes
HyperAgents replaces the 5 hardcoded thresholds in `_detect_revenue_bottlenecks()` with self-modifying evaluation criteria. Instead of `reply_rate_14d < 1.5` (line 44), a meta-evaluator agent periodically reviews whether thresholds should change based on historical performance.

### Complexity: MEDIUM

**Reason:** The change is localized to `expansion.py` and does not touch the LLM hot path. However, self-modifying evaluation criteria are conceptually dangerous -- a bad meta-evaluation could disable quality gates.

### Code Analysis

`expansion.py` lines 44-80: Five hardcoded conditions check metrics against fixed targets (`reply_rate < 1.5`, `interest_rate < 12.0`, `proposal_backlog >= 3`, `closed_uninvoiced >= 2`, and `email_revenue_ratio`). Each has a `current_value` and `target_value`. HyperAgents would make `target_value` dynamic, pulled from a `meta_evaluations` table instead of hardcoded.

The skill evaluation at the bottom of `expansion.py` (around line 300+) uses similar hardcoded ROI gates. These are the second integration point.

### LOC Estimate: 250-350
- `titan/meta_evaluator.py` (new): ~150 LOC -- meta-evaluation agent that reviews and proposes threshold changes
- `titan/expansion.py` (modify): ~60 LOC -- replace hardcoded thresholds with DB lookups
- `titan/memory.py` (modify): ~40 LOC -- store meta-evaluation decisions for audit trail

### New Dependencies
None. Uses existing LLM client + Postgres.

### Async Concern: LOW
Meta-evaluation runs on a scheduled cycle (daily/weekly), not in the pipeline hot path. Can be a background task in Perseus scheduler.

### Prerequisite Fixes
1. **Safety bounds:** Meta-evaluator must have hard floors and ceilings that cannot be overridden. Example: `reply_rate` target can range [0.5, 5.0] but never be set to 0 (which would disable the check). These safety bounds must be code-level constants, not DB-configurable.
2. **Audit trail:** Every threshold change must be logged with before/after values and the rationale. This is critical for debugging when the system starts making different expansion decisions.

### Database Migration
- New table: `meta_evaluations` (id, evaluation_id, criteria_name, value_before, value_after, outcome_metric, generation, rationale, created_at)

### Testing Strategy
- Unit: Test that safety bounds are enforced (meta-evaluator cannot set reply_rate target to 0)
- Integration: Run expansion cycle with meta-evaluated thresholds, verify bottleneck detection still produces sane outputs
- Regression: Compare bottleneck detection results before/after meta-evaluation integration over 14-day historical data

---

## 3. Anti-Slop -- Quality Gate

### Integration Surface
- **Primary:** `titan/pipeline/email_compose.py` (286 LOC) -- post-composition, pre-send
- **Secondary:** `clawdbot/site_builder.py` (1,779 LOC) -- site copy generation
- **Infrastructure:** None new

### What Changes
Add a scoring function that evaluates generated content on 5 dimensions (naturalness, specificity, conciseness, authenticity, overall) before it reaches the send stage. Content below threshold gets regenerated.

### Complexity: LOW

**Reason:** This is a pure addition with no modification to existing control flow. Email compose already has content validation (confirmed in code: `_load_soul_copy()` loads guidelines). Anti-slop is an additional check on the output, not a change to the generation process.

### Code Analysis

`email_compose.py` line 40-80: The compose loop iterates over leads and generates emails. The anti-slop check inserts between email generation (line ~100) and the `transition_lead()` call that moves the lead to "composed" status. If the score is below threshold, the email is regenerated with adjusted parameters (higher temperature, different skill).

`site_builder.py` lines 28-60: The 5-agent competitive build process already has a review step (`evaluate_site_experience()` at import line 21). Anti-slop scoring for copy would slot into this existing evaluation pipeline.

### LOC Estimate: 150-200
- `shared/quality_scorer.py` (new): ~100 LOC -- 5-dimension scoring function using LLM-as-judge
- `titan/pipeline/email_compose.py` (modify): ~30 LOC -- add score check + retry loop
- `clawdbot/site_builder.py` (modify): ~20 LOC -- add copy scoring to review step

### New Dependencies
None. Uses existing LLM client for scoring.

### Async Concern: NONE
All integration points are already async. The scorer is a single LLM call -- same pattern as every other `await llm.generate()` in the pipeline.

### Prerequisite Fixes
None. This is a clean addition.

### Database Migration
- New table: `quality_scores` (id, content_type, content_id, naturalness, specificity, conciseness, authenticity, overall, model_used, created_at)
- Standalone table (not JSONB on pipeline_tasks) to enable trend analysis and A/B testing

### Testing Strategy
- Unit: Test scorer with known-good and known-bad email samples, verify scores correlate with quality
- Integration: Run full email compose cycle, verify low-scoring emails get regenerated
- Regression: Score existing email templates from `soul/templates/` to establish baseline
- A/B: Compare reply rates on scored vs unscored emails over 2-week period

---

## 4. DeerFlow -- Persistent Daemon Memory

### Integration Surface
- **Primary:** `shared/agent_base.py` (270 LOC) -- `AgentBase` class
- **Secondary:** All 4 daemon.py files (Perseus, Titan, Hermes, ClawdBot)
- **Infrastructure:** Postgres + JSON file cache

### What Changes
Each daemon gets a persistent memory store that survives restarts. Currently, daemon state resets on every restart -- learnings, preferences, and runtime decisions are lost. DeerFlow adds a `daemon_memory` table and a JSON file cache for fast startup.

### Complexity: MEDIUM

**Reason:** The integration point (`AgentBase`) is the parent class for all 4 daemons. Changes here propagate everywhere. However, the change is additive -- adding `self._memory` to `__init__`, `_load_memory()` to `start()`, and `_save_memory()` to `stop()`.

### Code Analysis

`agent_base.py` lines 34-51: `__init__` sets up logger, running state, work tracking, and OJ EventBus. DeerFlow adds a `self._memory: DaemonMemory` attribute initialized from DB on startup.

`agent_base.py` lines 68-101: `register()` sets up agent in DB and creates Conway wallet. `_load_memory()` would run after `register()` in the `start()` sequence.

All 4 daemon classes inherit from `AgentBase`:
- `perseus/daemon.py:69` -- `PerseusDaemon(AgentBase)`
- `titan/daemon.py:131` -- `TitanDaemon(AgentBase)`
- `hermes/daemon.py:25` -- `HermesDaemon(AgentBase)`
- `clawdbot/daemon.py:246` -- `ClawdBotDaemon(AgentBase)`

### LOC Estimate: 300-400
- `shared/daemon_memory.py` (new): ~180 LOC -- DaemonMemory class with DB read/write, JSON cache, fact CRUD
- `shared/agent_base.py` (modify): ~60 LOC -- add memory lifecycle to init/start/stop
- Daemon files (4x ~15 LOC each): ~60 LOC -- daemon-specific memory categories and bootstrap facts

### New Dependencies
None. Uses existing Postgres + stdlib `json`.

### Async Concern: LOW
Memory load is async (DB query) but happens once at startup, not in hot path. Memory writes use write-through pattern: sync JSON file write + async DB write. The JSON write is fast (< 1ms for typical memory size) and does not block the event loop.

### Prerequisite Fixes
1. **Graceful shutdown:** `agent_base.py` has `stop()` as abstract. DeerFlow needs `_save_memory()` to run during shutdown. Must verify all 4 daemons call `super().stop()` or add it.

### Database Migration
- New table: `daemon_memory` (id, daemon_name, fact_content, category, confidence, source, created_at, updated_at)
- Index on `daemon_name` for fast per-daemon queries

### Testing Strategy
- Unit: Test memory CRUD operations, JSON cache consistency with DB
- Integration: Start daemon, store facts, stop daemon, restart daemon, verify facts survived
- Stress: Store 1000 facts, verify startup time stays under 500ms (JSON cache hit path)

---

## 5. Agent DNA -- Engineering Principles Injection

### Integration Surface
- **Primary:** `shared/llm_client.py` (418 LOC) -- `generate()` method, `system` parameter
- **Secondary:** `soul/` directory (4 files: soul_agent.md, soul_copy.md, soul_hermes.md, soul_values.md)
- **Tertiary:** `shared/skill_loader.py` (159 LOC) -- skill trust model

### What Changes
Create a shared engineering DNA document (compliance gates, budget awareness, quality requirements, security practices) that gets prepended to the `system` parameter of every LLM call. Currently, each daemon constructs its own system prompts independently with no shared engineering principles.

### Complexity: LOW-MEDIUM

**Reason:** The `system` parameter in `generate()` (line 70) is already the injection point. DNA injection is a string concatenation. However, the skill loader at `shared/skill_loader.py` trusts all files on disk (line 30-56: `find_skill()` reads any SKILL.md it finds). If DNA is loaded the same way, a tampered DNA file could inject malicious instructions into every LLM call.

### Code Analysis

`llm_client.py` line 67-77: `generate()` accepts `system: str = ""`. DNA injection means changing the default or adding a `_prepend_dna(system)` helper that loads DNA from `soul/engineering_dna.md` and prepends it. This is ~10 LOC in `llm_client.py`.

`skill_loader.py` lines 30-56: `find_skill()` searches 4 directories, reads any file matching the name. No checksum, no signature, no verification. If DNA is stored as a skill, it inherits this trust-everything model.

`soul/` directory: 4 existing files provide personality and copywriting guidelines. DNA fits as `soul/engineering_dna.md` -- same pattern, same trust model.

### LOC Estimate: 200-250
- `soul/engineering_dna.md` (new): ~80 LOC -- the DNA document itself (compliance, budget, quality, security principles)
- `shared/llm_client.py` (modify): ~30 LOC -- DNA loader + prepend logic with caching
- `shared/dna_verifier.py` (new): ~90 LOC -- SHA-256 checksum verification for DNA files (prerequisite fix for skill trust gap)

### New Dependencies
None. Uses stdlib `hashlib`.

### Async Concern: NONE
DNA is loaded once at module import time and cached. The `system` prepend is string concatenation -- zero async concern.

### Prerequisite Fixes
1. **Skill verification (SECURITY):** `shared/skill_loader.py` trusts all files on disk. Before DNA goes through the same path, add SHA-256 checksum verification. Store expected hashes in a `dna_manifest.json` that is version-controlled. This is not optional -- DNA tampering would compromise every LLM call in the system.
2. **Token budget:** DNA text consumes system prompt tokens on every call. Must be concise (< 500 tokens) to avoid blowing context windows, especially for Haiku calls via Ollama where context is limited.

### Database Migration
None.

### Testing Strategy
- Unit: Test DNA loader caching (loads once, not per-call), checksum verification (rejects tampered files)
- Integration: Run pipeline with DNA enabled, verify emails and site copy reflect engineering principles
- Token audit: Measure system prompt token usage before/after DNA injection across all model tiers

---

## 6. PQC -- Post-Quantum Cryptography

### Integration Surface
- **Primary:** `conway/wallet.py` (283 LOC) -- `WalletManager`, `_get_keystore_password()`, `_create_new_wallet()`
- **Secondary:** `shared/config.py` -- `CONWAY_KEYSTORE_PASSWORD` env var
- **Infrastructure:** Keystore files at `conway/data/keystores/`

### What Changes
Replace the single shared keystore password (`CONWAY_KEYSTORE_PASSWORD`) with per-agent PQ-KEM key wrapping. Each agent gets a Kyber-768 keypair; the private key encrypts the wallet keystore instead of a shared password. This protects against harvest-now-decrypt-later quantum attacks.

### Complexity: HIGH

**Reason:** Conway handles real USDC on Base L2. Any bug in the crypto migration could lock agents out of their wallets permanently. The single-password model (`_get_keystore_password()` at line 270) is deeply embedded -- it is called by both `_create_new_wallet()` (line 237) and `_load_from_keystore()` (line 260). Migration requires a dual-key period where both old and new encryption are valid.

### Code Analysis

`wallet.py` lines 227-238: `_create_new_wallet()` calls `Account.encrypt(private_key, _get_keystore_password())` to produce an Ethereum keystore v3 file (scrypt + AES-128-CTR). PQC replaces this with:
1. Generate Kyber-768 keypair per agent
2. Use KEM to derive a shared secret
3. Use shared secret as keystore password (or replace keystore encryption entirely with PQ-KEM + AES-256)

`wallet.py` lines 255-265: `_load_from_keystore()` calls `Account.decrypt(encrypted, _get_keystore_password())`. During migration, this must try PQ decryption first, fall back to legacy password.

`wallet.py` line 270-278: `_get_keystore_password()` returns a single env var. PQC replaces this with per-agent key derivation from the agent's Kyber private key.

### LOC Estimate: 400-500
- `conway/pqc_keystore.py` (new): ~200 LOC -- Kyber-768 key generation, KEM encapsulation/decapsulation, keystore re-encryption
- `conway/wallet.py` (modify): ~80 LOC -- replace `_get_keystore_password()` with PQ key derivation, add migration path
- `conway/key_rotation.py` (new): ~120 LOC -- key rotation mechanism (does not exist today)
- `shared/config.py` (modify): ~20 LOC -- add PQ config (algorithm selection, migration mode flag)

### New Dependencies
| Package | Risk | Notes |
|---------|------|-------|
| `liboqs-python` | Medium-High | OQS project, NIST-approved algorithms (Kyber-768). Requires cmake + liboqs C library for build. Must verify macOS ARM64 (M4) build works. Binary wheels may not exist -- source compilation adds build complexity. |

### Async Concern: LOW
Crypto operations are CPU-bound, not I/O-bound. Kyber-768 KEM is fast (< 1ms per operation). Key generation happens once per agent. The wallet operations (`get_balance`, `send_usdc`) are already async HTTP calls -- the crypto layer sits underneath, not in the async path.

### Prerequisite Fixes (CRITICAL)
1. **Per-agent passwords (MUST FIX FIRST):** The current single-password model (`CONWAY_KEYSTORE_PASSWORD` shared across all agents) must be broken into per-agent keys BEFORE PQC. Migrating from one shared password to per-agent PQ keys in a single step is too risky. Step 1: per-agent passwords. Step 2: PQ key wrapping.
2. **Key rotation mechanism:** No key rotation exists in Conway today. PQC keys have recommended rotation periods. Build rotation infrastructure first, then PQC uses it.
3. **Backup/recovery:** If a PQ private key is lost, the wallet keystore is irrecoverable. Must add key escrow or multi-key recovery before deploying PQC to production wallets.

### Database Migration
- New table: `encrypted_keys` (id, wallet_id FK, ciphertext_hex, encrypted_key, algorithm, pq_public_key_hex, migration_status, created_at)
- Modify `agent_wallets`: Add `encryption_version INT DEFAULT 1` (1=legacy, 2=pq-hybrid, 3=pq-only)

### Testing Strategy
- Unit: Test Kyber-768 key generation, KEM encapsulation/decapsulation round-trip
- Integration: Create wallet with PQ encryption, load wallet, verify private key matches
- Migration: Test dual-key period -- wallet encrypted with legacy password can be re-encrypted with PQ, then decrypted with PQ
- Regression: All existing wallet operations (balance check, send USDC) work unchanged after PQ migration
- Build: Verify `liboqs-python` compiles on macOS ARM64 (M4) in CI

---

## 7. Quantum Computing -- SCOPED OUT

Per the integration brief, quantum portfolio optimization (Amazon Braket, 20-50 asset QAOA) is future work for the trading project, not Objective Hertz. Noted for cross-project awareness only. No technical work required.

---

## Critical Blockers

### Blocker 1: WorkflowEngine.run() is Sync

**File:** `openjarvis/workflow/engine.py` line 39
**Impact:** RLM integration, DeerFlow middleware, any future cross-cutting concern

The `run()` method is synchronous. It uses `concurrent.futures.ThreadPoolExecutor` (line 87) for parallel nodes, but the outer method blocks the calling thread. This means:
- Any middleware that needs to intercept workflow execution must be sync
- RLM recursive calls from within a workflow node would block the thread pool
- DeerFlow memory writes during workflow execution require `asyncio.to_thread()`

**Recommended fix:** Add `async def arun()` to WorkflowEngine that uses `asyncio.gather()` for parallel nodes instead of ThreadPoolExecutor. Keep `run()` for backward compatibility. Estimate: 80-100 LOC.

**When to fix:** Before RLM integration (Integration 1). Not needed for Anti-Slop, HyperAgents, DNA, or PQC (these do not go through WorkflowEngine).

### Blocker 2: No Skill Verification

**File:** `shared/skill_loader.py` lines 30-56
**Impact:** Agent DNA integration (Integration 5)

`find_skill()` reads any SKILL.md file from 4 directories with no verification. DNA files must be integrity-checked before injection into system prompts.

**Recommended fix:** Add SHA-256 checksum manifest for DNA files. Can be lightweight -- does not need full PKI. Estimate: 90 LOC in `shared/dna_verifier.py`.

**When to fix:** Before Agent DNA integration (Integration 5).

### Blocker 3: Single Shared Keystore Password

**File:** `conway/wallet.py` line 270
**Impact:** PQC integration (Integration 6)

All agent keystores use one password from `CONWAY_KEYSTORE_PASSWORD`. PQC per-agent keys require per-agent passwords first.

**Recommended fix:** Derive per-agent passwords from the master password + agent name using HKDF. Backward-compatible migration: re-encrypt existing keystores during first run. Estimate: 60 LOC.

**When to fix:** Before PQC integration (Integration 6).

### Blocker 4: No Key Rotation in Conway

**File:** `conway/wallet.py` -- absent feature
**Impact:** PQC integration (Integration 6)

No mechanism to rotate wallet encryption keys. PQC keys need rotation. Without rotation infrastructure, PQC deployment is a one-shot operation with no recovery path.

**Recommended fix:** Add `conway/key_rotation.py` with scheduled rotation. Estimate: 120 LOC.

**When to fix:** Before PQC integration (Integration 6).

---

## Recommended Implementation Order

The dependency graph dictates this order:

```
Phase 0 (Prerequisite Fixes)
  0a. WorkflowEngine.arun()           [Unblocks: RLM]
  0b. Skill/DNA checksum verification  [Unblocks: Agent DNA]
  0c. Per-agent keystore passwords     [Unblocks: PQC]
  0d. Key rotation mechanism           [Unblocks: PQC]

Phase 1 (Zero-Dependency Integrations -- parallel)
  1a. Anti-Slop Quality Gate           [No prerequisites, lowest risk]
  1b. Agent DNA                        [Depends on 0b]
  1c. DeerFlow Persistent Memory       [No prerequisites]

Phase 2 (Medium-Dependency Integrations)
  2a. HyperAgents Meta-Evaluation      [Benefits from Anti-Slop for quality scoring]
  2b. RLM Recursive Context            [Depends on 0a, benefits from DeerFlow]

Phase 3 (High-Risk Integration)
  3a. PQC Post-Quantum Crypto          [Depends on 0c + 0d, highest risk]
```

### Rationale

1. **Phase 0 first** because blockers affect multiple integrations. The 4 fixes are independent and can be parallelized.

2. **Anti-Slop first in Phase 1** because it is the lowest risk (pure addition, no existing code modification), provides immediate measurable value (email quality scores), and its `quality_scores` table feeds HyperAgents in Phase 2.

3. **Agent DNA parallel with Anti-Slop** because DNA + Anti-Slop together give the system both engineering principles and quality enforcement -- a strong baseline before adding self-modification (HyperAgents).

4. **DeerFlow parallel in Phase 1** because persistent memory benefits every subsequent integration. RLM queries are better when daemon memory provides context. HyperAgents decisions persist across restarts.

5. **HyperAgents in Phase 2** because it needs Anti-Slop's quality scoring infrastructure to evaluate whether threshold changes actually improve output quality.

6. **RLM in Phase 2** because it depends on the async WorkflowEngine fix and benefits from DeerFlow memory for context enrichment.

7. **PQC last** because it has the most prerequisites (per-agent passwords + key rotation), the highest risk (wallet lockout), and the lowest urgency (HNDL attacks are theoretical, not imminent). Getting the revenue pipeline improvements (Phases 1-2) deployed first maximizes business value while PQC prerequisites are built.

### Time Estimate

| Phase | Calendar Time | Parallel Tracks |
|-------|-------------|----------------|
| Phase 0 | 2-3 days | 4 parallel fixes |
| Phase 1 | 3-5 days | 3 parallel integrations |
| Phase 2 | 5-7 days | 2 parallel integrations |
| Phase 3 | 5-7 days | 1 serial integration (crypto demands caution) |
| **Total** | **15-22 days** | |

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| RLM burns budget (recursive calls multiply cost) | High | High | Budget context grouping in _budget_gate(), hard cap per RLM chain |
| liboqs-python fails to build on M4 | Medium | High (blocks PQC) | Test build in CI before planning PQC sprint |
| HyperAgents sets bad thresholds | Medium | Medium | Hard safety bounds as code-level constants |
| DNA increases token usage beyond context limits | Low | Medium | Keep DNA < 500 tokens, test with Haiku context window |
| DeerFlow JSON cache gets corrupted | Low | Low | DB is source of truth, JSON is warm cache only |
| Anti-Slop scoring is miscalibrated | Medium | Low | A/B test scored vs unscored emails before enforcing |

---

## Appendix: File Inventory

Files that will be created or modified per integration:

```
Integration 1 (RLM):
  NEW:    shared/rlm_client.py
  MODIFY: shared/llm_client.py
  MODIFY: titan/memory.py
  MODIFY: titan/pipeline/email_compose.py
  MIGRATE: leads table (add mem0_context_id)

Integration 2 (HyperAgents):
  NEW:    titan/meta_evaluator.py
  MODIFY: titan/expansion.py
  MODIFY: titan/memory.py
  MIGRATE: new meta_evaluations table

Integration 3 (Anti-Slop):
  NEW:    shared/quality_scorer.py
  MODIFY: titan/pipeline/email_compose.py
  MODIFY: clawdbot/site_builder.py
  MIGRATE: new quality_scores table

Integration 4 (DeerFlow):
  NEW:    shared/daemon_memory.py
  MODIFY: shared/agent_base.py
  MODIFY: perseus/daemon.py (minor)
  MODIFY: titan/daemon.py (minor)
  MODIFY: hermes/daemon.py (minor)
  MODIFY: clawdbot/daemon.py (minor)
  MIGRATE: new daemon_memory table

Integration 5 (Agent DNA):
  NEW:    soul/engineering_dna.md
  NEW:    shared/dna_verifier.py
  MODIFY: shared/llm_client.py

Integration 6 (PQC):
  NEW:    conway/pqc_keystore.py
  NEW:    conway/key_rotation.py
  MODIFY: conway/wallet.py
  MODIFY: shared/config.py
  MIGRATE: new encrypted_keys table, modify agent_wallets table

Prerequisite Fixes:
  MODIFY: openjarvis/workflow/engine.py (add arun)
  NEW:    shared/dna_verifier.py (covered in Integration 5)
  MODIFY: conway/wallet.py (per-agent passwords, covered in Integration 6)
  NEW:    conway/key_rotation.py (covered in Integration 6)
```

Total new files: 8
Total modified files: 15
Total DB migrations: 4 new tables + 3 column additions
