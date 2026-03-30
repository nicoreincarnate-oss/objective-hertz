# Concerns & Risk Analysis

**Analysis Date:** 2026-03-29
**Focus:** Risks and blockers specific to integrating 7 intel references (RLMs, HyperAgents, Anti-Slop, DeerFlow, Agent DNA, PQC, Quantum Computing)

---

## Critical Blockers for Intel Integration

### 1. `run_pipeline()` Is Synchronous (Blocks Middleware Phase 4)

- **Files:** `titan/workflow_pipeline.py` (line 144-180), `openjarvis/workflow/engine.py` (line 39)
- **Issue:** `run_pipeline()` calls `engine.run()` which is a synchronous method (`def run`, not `async def run`). This function blocks the calling thread for the entire DAG execution (10 stages: assess through invoice).
- **Impact on DeerFlow/Middleware:** Any middleware chain injected around pipeline stages cannot use async patterns (e.g., async memory retrieval, async quality gates). The `WorkflowEngine.run()` would need to become async-aware or the middleware must be sync-only.
- **Severity:** HIGH -- this is a structural constraint that limits all middleware-pattern integrations.

### 2. No Skill Verification (Blocks Agent DNA Phase 1)

- **Files:** `shared/skill_loader.py` (lines 30-56, 81-112)
- **Issue:** Skills are loaded from 4 directories (including user home dirs `~/.hermes/skills/`, `~/.openclaw/skills/`) and executed directly as LLM system prompts with zero verification. No GPG signature check, no content hash, no trust level, no sandboxing.
- **Impact on Agent DNA:** DNA injection via skill system means any malicious skill can override agent identity/behavior. If DNA is loaded as a skill, it inherits this trust gap.
- **Severity:** HIGH -- Agent DNA must be tamper-proof to be meaningful. Current skill loader trusts anything on disk.

### 3. JSONB Write Bug in Survival Monitor (Blocks PQC Phase 6)

- **Files:** `conway/survival.py` (line 153)
- **Issue:** The `_apply_tier_change` method writes tier values to `system_config` using `_JsonbType(new_tier)` when psycopg's `Jsonb` is available, but falls back to writing a raw string when it is not. The `get_inference_override` function (line 238) then reads from the same table and does `tier = tier.strip('"')` to handle both cases. This dual-path serialization is fragile.
- **Impact on PQC:** Conway wallet operations depend on correct tier enforcement. If tier state is corrupted, the survival monitor could allow spending when it should be blocking (or vice versa). PQC key rotation during tier transitions would compound this risk.
- **Severity:** MEDIUM -- works in practice when psycopg is installed, but the fallback path is untested.

---

## Conway Wallet State (for PQC Phase 6)

### Current Key Storage Mechanism

- **Files:** `conway/wallet.py` (lines 227-243, 245-267)
- **Storage:** Encrypted JSON keystores on disk at `conway/data/keystores/{agent_name}.json`
- **Encryption:** Uses `eth_account.Account.encrypt(private_key, password)` -- standard Ethereum keystore v3 (scrypt + AES-128-CTR)
- **Password source:** Single env var `CONWAY_KEYSTORE_PASSWORD` shared across all agent keystores (line 270-278). No per-agent passwords.
- **DB registration:** `conway_wallets` table stores `(agent_name, chain, public_address, keystore_ref)` -- keystore_ref is just `{agent_name}.json`

### Known Bugs

1. **Single shared password for all keystores:** `_get_keystore_password()` at line 270 returns one password for every agent. Compromising this single env var exposes all agent private keys.
2. **Keystore re-creation on missing file:** `_load_from_keystore()` at line 253-256 silently creates a NEW wallet if the keystore file is missing, which would generate a new address while the DB still has the old one registered. The old address's funds become inaccessible.
3. **No key rotation mechanism:** There is no function to rotate private keys, re-encrypt keystores, or migrate to a new encryption scheme. PQC migration will need to add this from scratch.

### Migration Risk Assessment for PQC

- **Risk: HIGH.** The current scheme (Ethereum keystore v3 with scrypt) is classical crypto. PQC Phase 6 requires:
  1. A new keystore format supporting post-quantum algorithms
  2. A migration path that re-encrypts existing keystores without losing funds
  3. Per-agent key derivation (currently missing)
  4. The `CONWAY_KEYSTORE_PASSWORD` env var pattern does not support key hierarchies needed for PQC
- **Prerequisite fix:** Add per-agent password derivation and key rotation BEFORE attempting PQC migration

---

## Email Pipeline State (for Anti-Slop Phase 2 + RLM Phase 5)

### Current email_compose.py Flow

- **Files:** `titan/pipeline/email_compose.py` (lines 40-229)
- **Context assembly chain:**
  1. Fetches leads from `clients` WHERE `status = 'researched'` (line 50-57)
  2. Retrieves learnings via `get_relevant_learnings()` from `titan.memory` (line 60-63)
  3. Fetches proven rules via `format_rules_for_prompt()` (line 67-68)
  4. Loads soul copy from `soul/soul_copy.md` (line 69)
  5. Computes prompt version hash for causal attribution (line 72-76)
  6. Passes all context into either skill-based or direct LLM composition

### Content Validation That Already Exists

- **Files:** `titan/pipeline/email_compose.py` (lines 238-286)
- `validate_email_content()` checks:
  - 6 fabricated claim patterns (line 241-248) -- catches "we've helped 500+ businesses" etc.
  - 7 spam trigger patterns (line 251-259) -- catches "ACT NOW", "CLICK HERE", etc.
  - Length checks: subject >80 chars, body >1000 chars or <30 chars
- **Also in email_send.py:** `_simulate_email_micro_review()` (lines 84-134) runs 3 persona simulations (skeptical_owner, time_starved_owner, deliverability_guard) with scoring

### Insertion Points for Anti-Slop Quality Gate

1. **After LLM generation, before JSON parse** (`email_compose.py` line 193-198): Best point for full anti-slop analysis. The raw LLM output is available before it gets parsed into subject/body.
2. **After `validate_email_content()`, before DB insert** (`email_compose.py` line 206-214): Existing validation already gates here. Anti-slop could replace or augment this check.
3. **In `_simulate_email_micro_review()`** (`email_send.py` line 84): The persona simulation is a natural fit for anti-slop personas. Could add a "quality_gate" persona.
4. **System prompt injection** (`email_compose.py` line 159-191): Anti-slop rules could be injected into the soul copy or rules block.

### Where Mem0 Context Could Be Injected (for RLM Phase 5)

- **Current Mem0 usage:** `get_relevant_learnings()` in `titan/memory.py` already queries vector memory
- **Injection point:** Between line 60-68 in `email_compose.py`, where `learned_tips` and `rules_block` are assembled. RLM could add a "recursive context expansion" step here that iteratively deepens the context.
- **Risk:** Token budget. The prompt at line 159 already includes soul_copy + learned_tips + rules_block + lead info. Adding RLM recursive context could blow past the 2048 `max_tokens` output limit or the model's context window. Budget gating in `shared/llm_client.py` (line 179-206) would auto-downgrade to Ollama if costs spike.

---

## Expansion Engine State (for HyperAgents Phase 7)

### Current Evaluation Criteria in expansion.py

- **Files:** `titan/expansion.py` (lines 40-115)
- `_detect_revenue_bottlenecks()` uses 5 hardcoded threshold rules:
  1. Reply rate <1.5% with >100 emails sent (14d) -> low_reply_rate
  2. Interest rate <12% with >15 replies (14d) -> low_interest_rate
  3. Proposal backlog >=3 with >=5 interested -> proposal_backlog
  4. Closed uninvoiced >=2 -> invoice_delay
  5. Discovered missing email >=10 (7d) -> missing_contact_data
- `_gate_candidate()` enforces: revenue_gain > 0, cost < budget, revenue > cost, ROI >= min_roi, capability_type in {skill, prompt, tool, agent}

### Shadow Rollout Mechanism

- **Files:** `titan/expansion.py` (lines 242-317, 377-489)
- Shadow rollout is discovery-skill-only: only `stage=lead_discovery` + `capability_type=skill` + skill exists + no active shadow -> auto-promotes to "shadow" status
- Shadow evaluation (`_evaluate_active_shadow_discovery`, line 377): compares interested_rate and close_rate of shadow leads (tagged via `source_campaign`) vs baseline
- Shadow percentage configurable via `expansion_shadow_percent` config (default 10%)
- Rejection if shadow leads produce zero interest/close; adoption if shadow >= baseline

### What Needs to Become Self-Modifying for HyperAgents

1. **Threshold values are hardcoded:** The 5 bottleneck thresholds (reply_rate <1.5%, interest_rate <12%, etc.) at lines 44-92 are constants. HyperAgents would need these to be DB-stored and self-adjusting.
2. **ROI gate is static:** `_gate_candidate()` at line 97 uses fixed min_roi and budget. Self-modification would need these to evolve based on historical accuracy.
3. **Shadow rollout is single-skill only:** Only one shadow experiment runs at a time (line 248-249 checks `not active_shadow_skill`). HyperAgents would need concurrent A/B experiments.
4. **No feedback loop on threshold accuracy:** The system evaluates shadow experiments but never updates its own bottleneck detection thresholds based on outcomes.
5. **LLM-dependent candidate generation:** The expansion review prompt at line 169 asks an LLM to propose capabilities. HyperAgents would need to inject learned heuristics here.

---

## Daemon Startup State (for DeerFlow Memory Phase 3)

### How Each Daemon Currently Loads State on Startup

| Daemon | File | State Loading | What's Loaded |
|--------|------|---------------|---------------|
| Perseus (legacy) | `perseus/daemon.py` line 79-95 | `db.init_pool()` + load schedules from `SCHEDULES` constant + init `_last_run` to `time.time()` | Schedule timing only. No persistent memory of previous run state. |
| Orchestrator | `orchestrator.py` line 135-148 | Creates fresh `Orchestrator()` with all fields set to `None`/empty. State built during `start()`. | No cross-restart persistence. |
| Titan | `titan/daemon.py` line 140-153 | `db.init_pool()` + compliance check + `requeue_stale_tasks()` + register | Requeues stale tasks (partial recovery), but no memory of previous decisions or learnings. |
| Hermes | `hermes/daemon.py` line 34-47 | `db.init_pool()` + register | No state recovery. `_forwarded_ids` set starts empty on restart (line 107). |
| ClawdBot | `clawdbot/daemon.py` line 33-70 | Agent mesh specs defined as constants | No persistent state across restarts. |

### What's Persisted vs Lost on Restart

**Persisted (in Postgres):**
- Task queue (`task_queue` table) -- Titan requeues stale tasks
- Pipeline state (client statuses, email sequences, deals)
- System config (`system_config` table) -- tier states, config flags
- Events (`events` table)
- Learnings (`titan_learnings` table)

**Lost on restart:**
- `_last_run` timing dict in Perseus (line 76-77) -- all schedules fire immediately after restart
- `_forwarded_ids` in Hermes (line 107) -- duplicate message forwarding on restart
- `_current_tiers` in SurvivalMonitor (line 89) -- all agents re-evaluated from scratch
- In-memory LLM client state (`_last_usage` in `shared/llm_client.py` line 59)
- Any in-flight async operations

### Where memory.json Pattern Could Hook In

1. **Titan daemon `_process_task_queue`** (`titan/daemon.py` line 215): Before processing tasks, load DeerFlow memory context for decision enrichment
2. **Perseus `_strategic_tick`** (`perseus/daemon.py` line 195): Strategic decisions currently use `_decide_priorities()` with no memory. DeerFlow could inject historical decision outcomes
3. **`shared/agent_base.py`**: The `AgentBase` class is the common parent. Adding a `_load_memory()` / `_save_memory()` lifecycle hook in `start()` / `finalize_shutdown()` would affect all daemons uniformly
4. **`openjarvis/agents/monitor_operative.py`**: Already has `session_store` and `memory_backend` patterns (line 11-12). DeerFlow could extend this to daemon-level persistence.

---

## LLM Client State (for Agent DNA Phase 1)

### How System Prompts Are Currently Constructed

- **Files:** `shared/llm_client.py` (lines 67-124, 245-283)
- The `generate()` method accepts an optional `system: str = ""` parameter
- System prompt is passed directly to Claude API as `body["system"]` (line 261-262) or to Ollama as `body["system"]` (line 377-378)
- **No global system prompt.** Each caller constructs its own system prompt:
  - `email_compose.py` builds system prompt inline in the prompt string (line 159-191)
  - `skill_loader.py` passes skill content as system prompt (line 107-109)
  - `expansion.py` uses inline prompt with no system param (line 169-208)

### Where DNA Could Be Injected Without Breaking Existing Calls

1. **In `LLMClient.generate()` at line 67-77:** Add a DNA preamble that prepends to any `system` parameter. This is the single chokepoint -- every LLM call flows through here.
   ```python
   # Injection point: after line 92 (model resolution)
   dna = await _load_agent_dna(agent_name)
   if dna:
       system = f"{dna}\n\n{system}" if system else dna
   ```
2. **In `_claude_generate()` at line 245:** Directly modify the messages/system before sending to Claude API.
3. **In `skill_loader.execute_skill()` at line 107:** The skill content is already used as `system=skill_content`. DNA could be prepended to skill content.

### Budget Gating Interaction with DNA Token Overhead

- **Files:** `shared/llm_client.py` (lines 179-206, 208-243)
- **Risk:** DNA preamble adds tokens to every call. Budget tracking at line 224 estimates `input_tokens = (len(prompt) + len(system)) / 4`. A 500-token DNA preamble across ~100 daily calls adds ~$0.05-0.30/day (Haiku) or ~$0.30-1.50/day (Sonnet).
- **Budget gate at line 179:** Checks monthly spend against cap. If DNA pushes spend over `alert_threshold` (default 80%), "fast" calls auto-downgrade to Ollama. Ollama has no token cost but also no DNA enforcement if DNA is only injected for Claude calls.
- **Recommendation:** DNA injection must happen in `generate()` before the Claude/Ollama routing decision, so it applies to both backends.

---

## Sync/Async Concerns (for Middleware Phase 4)

### Which Pipeline Stages Are Sync vs Async

**All pipeline stage functions are async:**
- `discover_leads()`, `research_leads()`, `compose_emails()`, `send_emails()`, `process_follow_ups()`, `process_interested_leads()`, `build_sites()`, `process_invoices()`, `sync_campaign_analytics()` -- all defined with `async def`

**The WorkflowEngine.run() is synchronous:**
- `openjarvis/workflow/engine.py` line 39: `def run(self, graph, system, *, initial_input, context)` -- not async
- This means when `run_pipeline()` in `titan/workflow_pipeline.py` line 164 calls `engine.run()`, it blocks the calling thread

**The Titan daemon task processing is async:**
- `titan/daemon.py` line 267: `_invoke_handler()` is `async def` and awaits handlers
- But `run_pipeline()` is never called from the daemon loop directly (the old `_run_pipeline_cycle` was removed per comment at line 374)

### Blocking Calls That Would Interfere with Middleware Chain

1. **`engine.run()` is synchronous** -- middleware that needs `await` (e.g., async DB lookups, async LLM calls for quality gates) cannot be used in the WorkflowEngine execution path without refactoring
2. **`httpx.AsyncClient` in wallet.py** -- wallet operations are async, but if called from a sync context they would fail
3. **`subprocess.run()` in training.py** (line 635) -- synchronous subprocess for SCP/SSH during LoRA training, blocks the event loop if called from async context

### run_pipeline() Blocking Issue

- **Files:** `titan/workflow_pipeline.py` line 144-180
- **Current state:** `run_pipeline()` is a sync function that calls `engine.run()` synchronously. It is NOT called from the Titan daemon loop (which uses task handlers instead).
- **Impact on Middleware Phase 4:** If middleware needs to wrap pipeline stages, it must work within the `WorkflowEngine.run()` execution model. Since `engine.run()` is sync, middleware hooks must either:
  1. Be synchronous (limiting)
  2. Use `asyncio.run()` internally (dangerous if already in an async context)
  3. Require `engine.run()` to be refactored to `async def run()`
- **Recommendation:** Refactor `WorkflowEngine.run()` to be async BEFORE attempting middleware integration

---

## Security Concerns

### Current Secret Management

- **Storage:** `.env` file at project root (noted as "LIVE API KEYS" in CLAUDE.md). Never committed to git.
- **Runtime access:** `shared/config.py` reads env vars at import time. `conway/wallet.py` reads env vars directly via `os.environ.get()`.
- **Dashboard key management:** `hermes/web/app.py` (lines 813-1257) allows updating API keys via the War Room dashboard, stored in `system_config` DB table AND set in `os.environ` for the running process.
- **No encryption at rest:** API keys in `.env` are plaintext. Keys stored in `system_config` via the dashboard are plaintext in Postgres. The only encrypted-at-rest storage is Conway keystores.

### External Skill Trust

- **Files:** `shared/skill_loader.py` (lines 22-27, 81-112)
- **No GPG verification.** Skills from `~/.hermes/skills/`, `~/.openclaw/skills/`, and `.agent/skills/` are loaded and executed without any signature check, hash verification, or content validation.
- **Arbitrary code execution:** Skills become LLM system prompts. A malicious skill could instruct the LLM to exfiltrate data, modify DB records, or bypass safety checks.
- **Impact on Agent DNA:** If DNA profiles are stored as skills, they inherit this trust gap. A compromised skill directory could override agent DNA.

### SSH with StrictHostKeyChecking=no

- **Files:** `titan/training.py` (line 629-632)
- **Issue:** LoRA training uses SSH/SCP to Vast.ai GPU instances with `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`. This disables MITM protection.
- **Mitigation:** The SSH connection is to dynamically provisioned GPU instances where host keys are ephemeral. However, this still allows DNS-based MITM attacks.
- **Impact on PQC Phase 6:** If PQC is applied to transport security, this SSH configuration must be updated to use PQ-safe key exchange.

### Auth Token Handling in Dashboard

- **Files:** `hermes/web/frontend/hooks/use-token.ts` (lines 1-39), `hermes/web/app.py` (lines 85-131)
- **Current state:** Token stored in `sessionStorage` (not localStorage -- good). Auth via `Bearer` header (not query params -- good). Backend uses `hmac.compare_digest` for timing-safe comparison (good).
- **Previous issue (now fixed):** Query-param auth was removed. The code explicitly comments that query params leak into browser history/referer headers.
- **Remaining risk:** The dashboard secret (`DASHBOARD_SECRET`) is a single shared secret for all users. No per-user auth, no session expiry, no CSRF tokens on state-changing requests.

---

## Technical Debt Overlap

### Existing Tech Debt That Are Prerequisites for Intel Integration

| Tech Debt Item | Files | Prerequisite For | Why |
|---|---|---|---|
| `WorkflowEngine.run()` is sync | `openjarvis/workflow/engine.py` | DeerFlow middleware (Phase 4) | Middleware hooks need async support for DB/LLM calls |
| No skill verification | `shared/skill_loader.py` | Agent DNA (Phase 1) | DNA must be tamper-proof; untrusted skills could override DNA |
| Single keystore password | `conway/wallet.py` line 270-278 | PQC (Phase 6) | PQC key derivation needs per-agent key hierarchy |
| No key rotation mechanism | `conway/wallet.py` | PQC (Phase 6) | Cannot migrate to PQ algorithms without rotation support |
| Hardcoded expansion thresholds | `titan/expansion.py` lines 44-92 | HyperAgents (Phase 7) | Self-modification requires DB-stored, learnable thresholds |
| No daemon state persistence | All `daemon.py` files | DeerFlow memory (Phase 3) | Cross-restart memory requires a persistence layer that does not exist |

### Intel Integrations That Naturally Fix Existing Tech Debt

| Integration | Fixes | How |
|---|---|---|
| Agent DNA (Phase 1) | Inconsistent system prompts across pipeline stages | DNA provides a single source of truth for agent identity/behavior |
| Anti-Slop (Phase 2) | email_compose.py validation is regex-only (no semantic understanding) | Anti-slop quality gate adds LLM-based content analysis beyond pattern matching |
| DeerFlow Memory (Phase 3) | All daemon state lost on restart (_last_run, _forwarded_ids, _current_tiers) | Cross-session memory persistence eliminates cold-start amnesia |
| HyperAgents (Phase 7) | Expansion engine cannot learn from outcomes | Self-modifying thresholds close the feedback loop on bottleneck detection |
| RLM (Phase 5) | email_compose context assembly is one-shot (no iterative deepening) | Recursive context expansion enables multi-hop reasoning for personalization |

---

*Concerns audit: 2026-03-29*
