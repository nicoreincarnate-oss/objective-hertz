# Scratch: worktree vs main module classification

Reference: worktree modules in `.claude/worktrees/charming-elion/shared/` vs main repo's `shared/`. Main's LLM surface is dominated by `llm_client.py` (1711L), `llm_unified.py`, `llm_factory.py`, `llm_providers/`.

## Key overlap findings

- **Main HAS**: `ModelTier` enum (llm_unified.py L20-29) with 7 tiers: genius/smart/fast/local/local-small/local-heavy/embed.
- **Worktree HAS**: `TierName` enum (tiers.py L30-49) with 11 tiers adding CODEX, AGENTIC, LONGCTX, CHAT, CHEAP, VISION — Phase 42.5 v2 taxonomy.
- **Main LACKS**: semantic cache, ML-based tier classifier, lead/worker pattern, spend_alerts (has budget_tracking table + middleware but no proactive threshold alerts), aider integration, voice I/O (Kokoro/Parakeet), imagegen (Draw Things), verifier subsystem, escalation log / redactor.

## Classification table

| File/Dir | Classification | Target in main | Rationale | Est. effort (h) |
|---|---|---|---|---|
| `shared/tiers.py` (494L) | **REFACTOR** | `shared/llm_unified.py` (merge into existing `ModelTier` + new `TierConfig`) | Overlaps main's `ModelTier` but adds Phase 42.5 v2 tiers (CODEX, AGENTIC, LONGCTX, CHAT, CHEAP, VISION) and richer `TierConfig` metadata. Cannot drop wholesale — main daemons depend on `ModelTier`. Must extend main's enum + preserve tier aliases for legacy callers (auto→smart semantics in worktree are incompatible with main's auto→fast). Decision point: do we rename main's `ModelTier` to `TierName` or keep both? | 4 |
| `shared/semantic_cache.py` (298L) | **PORT** | `shared/semantic_cache.py` (new file) | Net-new. Main has per-tier StickyLatch (prompt_builder) and prompt-cache boundary markers, but NO semantic/embedding cache with Redis backend and default-deny allowlist. Kept separate from `llm_client.py` so it's opt-in per call site. | 1 |
| `shared/tier_classifier.py` (451L) | **REFACTOR** | `shared/tier_classifier.py` (new, imports from main's `llm_unified.ModelTier` instead of `shared.tiers.TierName`) | Net-new concept (auto-tier picker) but `from shared.tiers import TierName` will break when merged into main. Must be rewired to import main's `ModelTier` after tiers.py refactor lands. Rules also reference vision/chat tiers that don't yet exist in main — depends on tiers.py extension. | 3 |
| `shared/lead_worker.py` (216L) | **REFACTOR** | `shared/lead_worker.py` (new, wire to main's `llm.generate()`) | Net-new agent loop pattern (lead=strong model, worker=cheap). Currently imports `from shared.tiers import TierName`; needs adapter to main's `ModelTier` and needs its internal tier→model resolution to call `shared/llm_client.py`'s singleton `llm.generate(prompt, model=...)` instead of a standalone tier router. Small surface, easy rewire. | 2 |
| `shared/spend_alerts.py` (189L) | **REFACTOR** | `shared/spend_alerts.py` (new) | Net-new proactive alerting (50/75/90/100% ladder, Hermes Telegram wiring). Main has `budget_tracking` table + middleware for blocking but NO threshold ladder + alerting. The scratch file references `tier_spend_log` and `daemon_budget_caps` tables that don't exist in main — schema migration required. Wire into main's `shared.middleware` check_budget path. | 3 |
| `shared/aider/` (3 files, 797L total) | **PORT** | `shared/aider/` (new dir) | Net-new Aider integration for Ruflo (code fix swarm) and Clawdbot (site builder). Uses architect+editor pattern. Depends on `lead_worker.py` abstractions landing first. No equivalent in main. | 3 |
| `shared/voice/` (4 files, 415L total) | **PORT** | `shared/voice/` (new dir) | Net-new Kokoro TTS client, Parakeet ASR client, intent router. Zero equivalent in main. Self-contained (HTTP clients to localhost daemons). Depends on Wave R1 launchd plists (already landed). | 2 |
| `shared/imagegen/` (2 files, 193L total) | **PORT** | `shared/imagegen/` (new dir) | Net-new Draw Things client. Self-contained HTTP client to localhost image gen daemon. No equivalent in main (main has Claude vision read-path but no generation path). | 1 |
| `shared/verifier/` (5 files, 938L total) | **REFACTOR** | `shared/verifier/` (new dir) | Net-new but partially broken in worktree (P0-9, P0-10 already fixed as linter-applied). Grammar compiler, depth guard, self-consistency vote, a2a callback. Must be wired to main's `llm.generate()` for verifier passes — currently likely calls its own tier router. Also integrates with main's agent_loop.py. Medium coupling. | 4 |
| `shared/escalation_log/` (2 files, 440L total) | **REFACTOR** | `shared/escalation_log/` (new dir) | Net-new escalation log + redactor (P0-5, P0-6 already fixed as linter-applied with scrypt KDF + fail-closed crypto). Wires into main's `log_redaction.py` existing surface — need to dedupe vs main's redaction path or merge. Operator decision on which redactor wins. | 3 |

## Effort totals by classification

| Class | Items | Total Hours |
|---|---|---|
| DELETE | 0 | 0 |
| PORT | 4 (semantic_cache, aider, voice, imagegen) | 7 |
| REFACTOR | 6 (tiers, tier_classifier, lead_worker, spend_alerts, verifier, escalation_log) | 19 |
| **Grand total** | **10** | **26 hours** |

## Cross-cutting risks

1. **TierName vs ModelTier** — worktree `tiers.py` and main `llm_unified.ModelTier` will collide. Must pick one name and merge the tier lists before anything downstream can be ported.
2. **Legacy alias conflict** — worktree maps `auto→smart`, main maps `auto→fast`. Behavior change for every daemon on first import.
3. **DB schema drift** — spend_alerts references `tier_spend_log` and `daemon_budget_caps` tables that may not exist in main's schema. Need migration script.
4. **P0-9/P0-10 verifier fixes already landed in worktree** — REFACTOR must preserve those fixes.
5. **P0-5/P0-6 redactor fixes already landed in worktree** — REFACTOR must preserve scrypt KDF and fail-closed crypto.

## Zero DELETE findings

Every worktree module has net-new capability over main. The duplication risk is in **concept overlap** (tier routing) rather than in identical code. The REFACTOR column captures all "conceptually overlaps with main, must be merged" cases.
