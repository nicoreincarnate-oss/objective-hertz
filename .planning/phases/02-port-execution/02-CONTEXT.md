# Phase 2: PORT Execution - Context

**Gathered:** 2026-04-08 (auto-generated, discuss skipped)
**Status:** Ready for planning
**Mode:** Auto-generated (skip_discuss=true + operator explicit "auto-advance")

<domain>
## Phase Boundary

Execute the PORT-PLAN.md recovery produced by Phase 1 Wave R2 investigation. Resolve P0-1 (stale llm_client.py rebase gap), P0-2 (ghost integration — no daemon imports), P0-3 (Ruflo sandbox runner missing), and 2 latent pre-existing bugs discovered during Wave R2 survey.

The source of truth: `docs/audits/pre-launch/PORT-PLAN.md` (191 lines, §1-8 is the recovery plan, §9 is the 7 operator decisions locked 2026-04-08).

Sequencing: risk-front-loaded (§6c) — rebase + latent bugs + security refactors + correctness refactors land FIRST so rollback is clean if CI breaks, then tier core merge, then sidecar PORTs.

</domain>

<decisions>
## Locked Operator Decisions (PORT-PLAN §9)

1. **`auto → smart`**: worktree's Sonnet upgrade wins (quality > cost rule)
2. **Keep `ModelTier`** name, extend with 4 new tiers (CODEX/AGENTIC/LONGCTX/CHAT/CHEAP/VISION) — lower change surface in main
3. **Semantic cache**: optional Redis via `REDIS_URL` env + in-process LRU fallback when unset
4. **Redactor winner**: worktree's `escalation_log/redactor.py` (has P0-5+P0-6 security fixes); migrate main's callers
5. **Spend-alert schema**: new `daemon_budget_caps` + `tier_spend_log` tables via migration 046 (already exists in worktree)
6. **Latent bugs**: ship fixes (clawdbot tier= + openjarvis ask_llm) as part of Phase 2 rather than file separately
7. **Tier additions**: enum-only land; provider wiring is Wave R3+ work

## Non-negotiable preservation

- **P0-5**: scrypt KDF in `shared/escalation_log/redactor.py` (already landed in Phase 1 commit `65b0ef5`)
- **P0-6**: crypto fail-closed in redactor (already landed `65b0ef5`)
- **P0-9**: self-consistency vote bug fix in `shared/verifier/consistency.py` (already landed `3ab29c1`)
- **P0-10**: grammar compiler raise on unknown features in `shared/verifier/grammar_compiler.py` (linter-applied)

These MUST survive the port/refactor passes. Verification: re-run the same import spot checks from 01-03.

</decisions>

<code_context>
## Existing Code Insights

**Main repo's `shared/llm_client.py`** (1711 lines at `intel-integration`):
- Tier resolver: `_resolve_model` at L91-127 with StickyLatch caching keyed `model_tier:{db_key}`
- Fallback chain: `_MODEL_FALLBACK_CHAIN` L77-82 (genius → smart → fast → local)
- Generate entry: `LLMClient.generate` L397-539 with signature `(prompt, *, system, model="auto", max_tokens=2048, temperature=0.7, client_id, pipeline_stage, use_dna, daemon_name) -> str`
- Death spiral guard: `_DeathSpiralGuard` L199-250
- Cost recording: `_record_claude_spend` L942-999 (IGUS-FIX: fail-closed)
- Module singleton: `llm` at L1711

**Main repo's `shared/llm_unified.py`** has `ModelTier` enum with 7 tiers. Phase 2 extends it with 4-6 new tiers per decision #2.

**Main repo's daemons** (from Wave R2 survey of 57 call sites):
- All use `llm.generate(prompt, model="<string>", daemon_name="...", pipeline_stage="...")` 
- No daemon references `shared.tiers`, `shared.semantic_cache`, etc. (that's P0-2, resolved by PORTs landing on main + daemons getting updated imports only if they want new capabilities)
- **2 latent bugs to fix as part of Phase 2**:
  - `clawdbot/a2a_server.py:276` uses `tier="fast"` instead of `model="fast"` → TypeError
  - `openjarvis/core/hooks.py:180` does `from shared.llm_client import ask_llm` but that symbol doesn't exist → ImportError

**Worktree modules to PORT** (4 net-new, copy-as-is):
- `shared/semantic_cache.py` (298 L)
- `shared/aider/` (ruflo_loop.py + clawdbot_loop.py + __init__.py, 797 L total)
- `shared/voice/` (parakeet_client.py + kokoro_client.py + intent_router.py + __init__.py, 415 L)
- `shared/imagegen/` (draw_things_client.py + __init__.py, 193 L)

**Worktree modules to REFACTOR** (6 adapted):
- `shared/tiers.py` (494 L) → merge into main's `shared/llm_unified.ModelTier`
- `shared/tier_classifier.py` (451 L) → rewire imports to ModelTier
- `shared/lead_worker.py` (216 L) → rewire imports; already has P1-7 deepcopy fix (preserve)
- `shared/spend_alerts.py` (189 L) → wire to main's `shared/middleware.py:check_budget_for_llm_call`
- `shared/verifier/` (5 files, 938 L) → rewire tier lookups; preserve P0-9 + P0-10
- `shared/escalation_log/` (2 files, 440 L) → dedupe vs main's `shared/log_redaction.py`; preserve P0-5 + P0-6

</code_context>

<specifics>
## Plan Split

**Plan 02-01 — Foundation + Security/Correctness** (~11h, blocks 02-02)
1. Rebase worktree `claude/charming-elion` onto `intel-integration`, resolve `shared/llm_client.py` by keeping main's 1711-line version (strategy: `git checkout main -- shared/llm_client.py` during rebase conflict)
2. Fix `clawdbot/a2a_server.py:276` — change `tier="fast"` to `model="fast"`
3. Fix `openjarvis/core/hooks.py:180` — change `from shared.llm_client import ask_llm` to `from shared.llm_client import llm`
4. REFACTOR `shared/escalation_log/` — dedupe vs `shared/log_redaction.py`. Keep worktree's redactor with P0-5 scrypt KDF + P0-6 fail-closed. Migrate any main callers of `log_redaction` to use the new module. Preserve canary test.
5. REFACTOR `shared/verifier/` — rewire any tier lookups to use `from shared.llm_unified import ModelTier` instead of `from shared.tiers import TierName`. Preserve P0-9 self-consistency fix and P0-10 grammar compiler fix. Verify with import spot checks.
6. REFACTOR `shared/spend_alerts.py` — wire into `shared/middleware.py:check_budget_for_llm_call`. Apply migration 046 if not already applied. Emit alerts via existing Hermes pipeline.

**Plan 02-02 — Tier Core** (~9h, depends on 02-01)
1. REFACTOR `shared/tiers.py` → merge `TierName` into main's `ModelTier` enum. Add 6 new tiers: CODEX, AGENTIC, LONGCTX, CHAT, CHEAP, VISION. Keep `TierConfig` dataclass as new metadata store (main has no equivalent). Pick `auto → smart` alias.
2. REFACTOR `shared/tier_classifier.py` → rewire imports from TierName to ModelTier. Verify rule keywords still map to merged tier names (vision/chat rules depend on #1 above).
3. REFACTOR `shared/lead_worker.py` → rewire tier imports. Preserve P1-7 deepcopy fix. Verify all 5 callback sites still work.

**Plan 02-03 — Sidecar PORTs** (~7h, depends on 02-02)
1. PORT `shared/semantic_cache.py` — copy to main with LRU fallback added (check `REDIS_URL` env, fall back to `functools.lru_cache(maxsize=1024)` when unset). Preserve strict allowlist.
2. PORT `shared/voice/` — copy 4 files to main. No adaptation needed.
3. PORT `shared/imagegen/` — copy 2 files to main. No adaptation needed.
4. PORT `shared/aider/` — copy 3 files to main. Requires `shared/lead_worker.py` refactor done first (Plan 02-02). Wire Ruflo sandbox runner as part of this port (P0-3): replace `sandbox_runner=None` default in `ruflo_loop.py` with a subprocess wrapper that invokes `sandbox-exec -D HOME=$HOME -f litellm/sandboxes/verifier.sb python -m pytest ...`.

Each plan commits atomically per task. Failures in any step: record in SUMMARY, continue to next task if independent, stop if blocking downstream.

</specifics>

<deferred>
## Deferred

- Provider wiring for new enum tiers (CODEX/AGENTIC/LONGCTX/CHAT/CHEAP/VISION) — stays as Wave R3+ work
- Redis setup for semantic cache production use — LRU fallback ships now, Redis added when operator approves the runtime dep
- F5-TTS voice cloning evaluation (backlog)
- AirLLM heavy tier activation (waiting on Samsung T9 NVMe)
- The 3 latent test failures from Phase 1 Wave R4 (pre-existing, not Phase 2 scope)

</deferred>
