# PORT-PLAN — Phase 42.5 v2 Recovery

> Operator-facing recovery plan for the stale-baseline P0s (P0-1, P0-2, P0-3).
> Built from `PORT-PLAN-scratch-{llm-client,diff,daemons}.md`. Read time: ~5 min.

## TL;DR

- **State:** worktree's `shared/llm_client.py` is 257 lines; main's is **1711 lines** with tier routing, graduated fallback chain, StickyLatch, budget fail-closed, watchdog, AirLLM/oLLM, cost tracking, death-spiral guard, DNA injection, prompt caching, SSE streaming, and vision. Worktree is the stale baseline; main is the truth.
- **Effort estimate:** ~26 engineer-hours (4 PORT = 7h, 6 REFACTOR = 19h). Two engineers can land this in ~1.5 days elapsed.
- **Recommended path:** **Rebase worktree onto main `intel-integration`**, resolve `shared/llm_client.py` by **keeping main unchanged**, then land Phase 42.5 v2 modules as additive PORTs/REFACTORs on top.
- **Blockers requiring operator decision:** (1) `TierName` vs `ModelTier` merge strategy, (2) `auto→smart` vs `auto→fast` alias conflict, (3) semantic cache Redis dependency, (4) escalation_log redactor vs main's `log_redaction.py` dedupe, (5) fix two pre-existing latent bugs (clawdbot `tier=` kwarg, openjarvis `ask_llm` ImportError) as part of REFACTOR pass.

## 1. Main repo llm_client.py reference

Source: `/Users/majovega/Desktop/Projects/objective-hertz/shared/llm_client.py` @ `intel-integration`.

| Feature | Lines | Notes |
|---|---|---|
| `ModelTier` alt via `_MODEL_MAP` | L99-109 | 9 tiers: genius/smart/primary/fast/local/local-small/local-heavy/airllm/embed |
| `_MODEL_FALLBACK_CHAIN` | L77-82 | genius → smart → fast → local |
| `_resolve_model` + StickyLatch | L91-127 | process-lifetime cache keyed `model_tier:{db_key}` |
| `_COST_PER_1K` | L131-135 | haiku=$0.001, sonnet=$0.006, opus=$0.045 |
| `_MAX_TOKENS_BY_STAGE` | L140-150 | per-stage output caps |
| `_build_system_blocks` | L156-196 | Anthropic prompt-cache boundary marker |
| `_DeathSpiralGuard` | L199-250 | 5 fails / 300s → 60s cooldown |
| **`LLMClient.generate` entry** | **L397-539** | `(prompt, *, system, model="auto", max_tokens=2048, temperature=0.7, client_id, pipeline_stage, use_dna, daemon_name) -> str` |
| `_generate_with_fallback_chain` | L551-643 | graduated; strips `<thinking>` when fallback from genius |
| `generate_stream` (SSE + watchdog) | L645-727 | 30/60/120s per tier |
| `generate_with_images` (vision) | L887-940 | raises on local, budget-downgrade raises |
| **`_record_claude_spend` fail-closed** | **L942-999** | IGUS-FIX: DB insert failure raises → caller downgrades |
| `_claude_generate` HTTP timeouts | L1001-1066 | genius=180s, fast=60s, default=120s |
| `_heavy_local_or_ollama_generate` | L1172-1206 | picks ollm vs airllm via policy |
| `_airllm_generate` + `_get_airllm_model` | L1208-1263 | lazy load via asyncio.to_thread |
| `_ollama_generate` (TurboQuant) | L1398-1433 | kv_cache_type + flash_attention opts |
| `LLMProtocol` | L1479-1506 | stable interface |
| `_CloudEngineWrapper` | L1509-1666 | CloudEngine w/ same cross-cutting |
| `UnifiedLLMFactory` | L1669-1692 | env-selected backend |
| Module singleton `llm` | L1711 | `from shared.llm_client import llm` |

Cost schema: `budget_tracking(month, category, amount, description, client_id, pipeline_stage)` — rounded to 4dp, ≥$0.001 threshold. CloudEngine wrapper inserts to `llm_spend(month, model, prompt_tokens, completion_tokens, cost_usd, pipeline_stage)`.

## 2. Files to DELETE

**None.** Every worktree module has net-new capability over main. Duplication is conceptual (tier routing) not textual. All conceptual overlaps are captured in §4 REFACTOR.

## 3. Files to PORT (net-new, copy-as-is)

| File/Dir | Size | Target in main | Effort |
|---|---|---|---|
| `shared/semantic_cache.py` | 298 L | `shared/semantic_cache.py` | 1 h |
| `shared/aider/` (3 files) | 797 L | `shared/aider/` (new dir) | 3 h |
| `shared/voice/` (4 files) | 415 L | `shared/voice/` (new dir) | 2 h |
| `shared/imagegen/` (2 files) | 193 L | `shared/imagegen/` (new dir) | 1 h |

**PORT total: 4 items, 7 hours.** Each is a straight `git add` in main after rebase — no main-side adaptation required.

## 4. Files to REFACTOR (adapt to main's API)

### 4a. `shared/tiers.py` (494 L) — 4 h
- Merge worktree `TierName` (11 tiers) into main `llm_unified.ModelTier` (7 tiers): add `CODEX`, `AGENTIC`, `LONGCTX`, `CHAT`, `CHEAP`, `VISION`.
- Unify legacy alias maps: decide `auto → smart` (worktree) vs `auto → fast` (main). **Operator decision point.**
- Preserve `TierConfig` dataclass as new file — main has no equivalent metadata store.

### 4b. `shared/tier_classifier.py` (451 L) — 3 h
- Rewrite imports: `from shared.tiers import TierName` → `from shared.llm_unified import ModelTier`.
- Verify rule keywords still map to merged tier names (vision/chat rules depend on 4a landing first).
- Add opt-in hook in `LLMClient.generate`: `auto_tier=True` → call classifier before `_resolve_model`.

### 4c. `shared/lead_worker.py` (216 L) — 2 h
- Rewire `from shared.tiers import TierName` → `from shared.llm_unified import ModelTier`.
- Replace internal tier→model resolution with `from shared.llm_client import llm; llm.generate(prompt, model=tier.value, ...)`.
- Preserve `LeadWorkerConfig`, `Step`, `StepResult`, `LeadWorkerResult` dataclasses as-is.

### 4d. `shared/spend_alerts.py` (189 L) — 3 h
- Add SQL migration for `daemon_budget_caps` and `tier_spend_log` tables (or map to existing `budget_tracking`).
- Wire into `shared/middleware.py` `check_budget_for_llm_call` to emit `SpendAlert` via Hermes.
- Integrate Telegram/War Room via `shared/pipeline_alerts.py`.

### 4e. `shared/verifier/` (5 files, 938 L) — 4 h
- **Preserve P0-9 + P0-10 fixes** (self-consistency vote laundering fix + grammar compiler raise on unknown features).
- Rewire verifier passes to call `llm.generate(model="smart")` instead of worktree's standalone tier router.
- Integrate `a2a_callback.py` with main's `agent_loop.py`.

### 4f. `shared/escalation_log/` (2 files, 440 L) — 3 h
- **Preserve P0-5 + P0-6 fixes** (scrypt KDF + fail-closed crypto).
- Dedupe `redactor.py` vs main's `shared/log_redaction.py` — **operator decision on which wins**.
- Merge redaction rule sets; ensure fail-closed semantics are preserved in merged version.

**REFACTOR total: 6 items, 19 hours.**

## 5. Daemon wiring changes

For every DAEMON call site, the current signature `llm.generate(prompt, model="<tier>", ...)` **remains valid** after REFACTOR because (a) 4a keeps the `model=` kwarg name and (b) tier names are additive. No daemon code needs to change for baseline functionality.

Targeted changes:

| Daemon | Changes | Sites |
|---|---|---|
| perseus | None for baseline; optionally add `auto_tier=True` in scout/self_audit | 0 required |
| titan | None for baseline; optionally promote 22 call sites to new tiers (CODEX for code-heavy pipeline, CHAT for summaries) | 0 required / up to 22 optional |
| hermes | None for baseline | 0 required |
| clawdbot | **Fix `a2a_server.py:276` `tier="fast"` → `model="fast"`** (pre-existing TypeError bug) | 1 required |
| conway | None — no direct LLM calls, only returns tier strings | 0 |
| deerflow_research | None for baseline; optionally wire LONGCTX for research | 0 required |
| ruflo | None for baseline; optionally wire to new `shared/aider/ruflo_loop.py` via REFACTOR 4c | 0 baseline / 4 optional |
| openjarvis | **Fix `core/hooks.py:180` `from shared.llm_client import ask_llm` → `from shared.llm_client import llm`** (pre-existing ImportError) | 1 required |

Only 2 **required** daemon edits — both are pre-existing bugs unrelated to Phase 42.5 v2.

## 6. Sequencing

### 6a. Recommended sequencing (single engineer, ~26 h)

1. **Rebase worktree onto `intel-integration`** — resolve `shared/llm_client.py` by keeping main's version. (0.5 h)
2. Land `shared/tiers.py` REFACTOR 4a — merge enums, pick alias strategy. (4 h) **Blocks 4b, 4c.**
3. Land `shared/tier_classifier.py` REFACTOR 4b. (3 h)
4. Land `shared/lead_worker.py` REFACTOR 4c. (2 h) **Blocks aider/ PORT.**
5. Land PORT `shared/semantic_cache.py`. (1 h)
6. Land PORT `shared/voice/`. (2 h)
7. Land PORT `shared/imagegen/`. (1 h)
8. Land PORT `shared/aider/` (depends on 4c). (3 h)
9. Land REFACTOR `shared/verifier/`. (4 h)
10. Land REFACTOR `shared/escalation_log/`. (3 h)
11. Land REFACTOR `shared/spend_alerts.py` + schema migration. (3 h)
12. Fix 2 latent daemon bugs (clawdbot tier=, openjarvis ask_llm). (0.5 h)

Total: **27 hours** elapsed for one engineer.

### 6b. Two-engineer split option

**Lane A — "Tier core" (Engineer 1, ~13 h)**
- Step 1 rebase (shared, blocks both lanes)
- Step 2 tiers.py REFACTOR
- Step 3 tier_classifier.py REFACTOR
- Step 4 lead_worker.py REFACTOR
- Step 11 spend_alerts.py REFACTOR

**Lane B — "Sidecars" (Engineer 2, ~13 h)**
- Step 5 semantic_cache.py PORT *(can start after rebase, independent of tier core)*
- Step 6 voice/ PORT
- Step 7 imagegen/ PORT
- Step 9 verifier/ REFACTOR *(independent — calls `llm.generate` directly)*
- Step 10 escalation_log/ REFACTOR
- Step 12 latent bug fixes

**Synchronization points:**
- After Step 1 (rebase), both engineers resume in parallel.
- Step 8 (aider/ PORT) runs **after** Lane A completes Step 4 — assign to whoever finishes first.

**Elapsed time: ~13 hours (≈1.5 working days).**

### 6c. Risk-front-loaded option (same 26 h, reordered)

Same work items, reordered so highest blast-radius lands first, enabling rapid rollback if any blows up:

1. **Rebase** (0.5 h) — defines everything downstream.
2. **Fix 2 latent daemon bugs** (0.5 h) — proves CI still works post-rebase.
3. **REFACTOR escalation_log/** (3 h) — P0-5 + P0-6 preservation; highest security risk.
4. **REFACTOR verifier/** (4 h) — P0-9 + P0-10 preservation; correctness-critical.
5. **REFACTOR spend_alerts.py** (3 h) — touches DB schema; rollback is painful post-deploy.
6. **REFACTOR tiers.py** (4 h) — blocks everything below it, land early.
7. **REFACTOR tier_classifier.py** (3 h)
8. **REFACTOR lead_worker.py** (2 h)
9. **PORT aider/** (3 h)
10. **PORT semantic_cache.py** (1 h)
11. **PORT voice/** (2 h)
12. **PORT imagegen/** (1 h)

**Rationale:** Security (P0-5/6) + correctness (P0-9/10) + DB schema changes land first. If CI fails anywhere in steps 3-5, we abort and the worktree stays on baseline rather than shipping a half-migrated state.

## 7. Effort summary

| Lane | Items | Hours |
|---|---|---|
| PORT (net-new copy-as-is) | 4 | 7 |
| REFACTOR (adapt to main API) | 6 | 19 |
| Latent bug fixes | 2 sites | 0.5 |
| Rebase + merge conflicts | 1 | 0.5 |
| **Grand total** | **13 work items** | **27 hours** |

Two-engineer elapsed: **~13 hours (1.5 working days).**

## 8. Open questions for operator

1. **`auto→smart` vs `auto→fast`:** worktree's `tiers.py` aliases `auto` to `smart` (Sonnet); main's `llm_client.py` aliases it to `fast` (Haiku). Picking worktree's version **silently doubles cost** on every `model="auto"` call in titan/hermes/clawdbot. Keep main's `auto→fast` or accept the upgrade?
2. **`TierName` vs `ModelTier`:** keep main's name `ModelTier` and extend it, or rename to worktree's `TierName` and update all imports? Main has 2 usages of `ModelTier`; worktree has 5. Main wins on change surface.
3. **Semantic cache Redis dep:** worktree's `semantic_cache.py` assumes Redis is running. Main has no Redis in the prod stack. Do we add Redis as a runtime dep (new launchd plist), use the dashboard's existing Redis, or make it optional with in-process LRU fallback?
4. **`log_redaction.py` vs `escalation_log/redactor.py`:** which redactor wins? Worktree's has scrypt KDF (P0-5 fix) and fail-closed crypto (P0-6 fix) — main's does not. Recommend worktree wins, but need explicit ack.
5. **Spend-alert schema:** create `daemon_budget_caps` + `tier_spend_log` tables (new) or adapt `spend_alerts.py` to use existing `budget_tracking` table (less powerful but zero migration)?
6. **Pre-existing bug fixes:** ship the 2 latent bug fixes (clawdbot `tier=` TypeError, openjarvis `ask_llm` ImportError) as part of this recovery, or file separate P2s and ignore here?
7. **Phase 42.5 v2 tier additions (CODEX/AGENTIC/LONGCTX/CHAT/CHEAP/VISION):** do these need corresponding provider wiring in `shared/llm_providers/` before merge, or land as enum-only and wire providers incrementally in Wave R3+?
