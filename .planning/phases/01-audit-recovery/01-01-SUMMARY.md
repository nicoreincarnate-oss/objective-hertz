---
phase: 01-audit-recovery
plan: 01
subsystem: audit-recovery
tags: [port-plan, wave-r1, wave-r2, launchd, llm-client, phase-42-5-v2]
dependency_graph:
  requires: []
  provides: [PORT-PLAN.md, 4-launchd-plists-verified]
  affects: [01-02-PLAN.md, 01-03-PLAN.md]
tech_stack:
  added: []
  patterns: [investigation-first, scratch-then-synthesize]
key_files:
  created:
    - docs/audits/pre-launch/PORT-PLAN-scratch-llm-client.md
    - docs/audits/pre-launch/PORT-PLAN-scratch-diff.md
    - docs/audits/pre-launch/PORT-PLAN-scratch-daemons.md
    - docs/audits/pre-launch/PORT-PLAN.md
    - scripts/launchagents/com.perseus.parakeet.plist
    - scripts/launchagents/com.perseus.kokoro.plist
    - scripts/launchagents/com.perseus.mem0.plist
    - scripts/launchagents/com.perseus.n8n.plist
  modified:
    - scripts/launchagents/com.perseus.n8n.plist
decisions:
  - Zero DELETE classifications — every worktree module adds net-new capability
  - Recommended path is rebase worktree onto main intel-integration, keep main's llm_client.py unchanged
  - 7 operator decision questions flagged for morning handoff
metrics:
  duration_minutes: ~25
  completed_date: 2026-04-07
requirements_completed: [P0-8, R2-investigation]
---

# Phase 01 Plan 01: Wave R1 Remainder + Wave R2 Investigation + PORT-PLAN Synthesis Summary

P0-8 launchd plists verified and normalized; Wave R2 investigation complete with PORT-PLAN.md delivered for operator approval of the Phase 42.5 v2 recovery path.

## What Was Verified

**4 launchd plists** (P0-8 — Wave R1 final quick-win):

| Plist | Status | Localhost bind | RunAtLoad | KeepAlive | Logs |
|---|---|---|---|---|---|
| `com.perseus.parakeet.plist` | already compliant | `127.0.0.1` via `--bind` | true | true | `~/Library/Logs/perseus/parakeet.log` |
| `com.perseus.kokoro.plist` | already compliant | `127.0.0.1` via `--bind` | true | true | `~/Library/Logs/perseus/kokoro.log` |
| `com.perseus.mem0.plist` | already compliant | `127.0.0.1` via `--bind` | true | true | `~/Library/Logs/perseus/mem0.log` |
| `com.perseus.n8n.plist` | **normalized** — added `N8N_HOST=127.0.0.1`, `N8N_LISTEN_ADDRESS=127.0.0.1`, changed `WEBHOOK_URL` from `localhost` → `127.0.0.1` | `127.0.0.1` via env | true | true | `~/Library/Logs/perseus/n8n.log` |

All 4 pass `plutil -lint`. No 0.0.0.0 binds anywhere. Placeholder XML comments preserved where daemons still point at pre-install paths.

## What Was Analyzed

### Main repo `shared/llm_client.py` (1711 lines)

Mapped all 9 required subsections with line citations: public API, tier routing (`_MODEL_MAP` L99-109, 9 tiers), fallback chain (`_MODEL_FALLBACK_CHAIN` L77-82, genius→smart→fast→local), StickyLatch cache (L114-127, process-lifetime keyed `model_tier:{db_key}`), budget gating (fail-closed, `_record_claude_spend` L942-999 raises on DB failure), watchdog/timeout (`_STREAM_WATCHDOG_TIMEOUTS` L55-60: 30/60/120s per tier), AirLLM integration (`_heavy_local_or_ollama_generate` L1172-1206, policy via `shared.airllm_policy`), cost tracking (`budget_tracking` table w/ schema `month/category/amount/description/client_id/pipeline_stage`).

**Primary entry point signature**: `LLMClient.generate(prompt, *, system, model="auto", max_tokens=2048, temperature=0.7, client_id, pipeline_stage, use_dna, daemon_name) -> str` at L397-539.

### Module classification (10 classification rows)

| Class | Items |
|---|---|
| DELETE | **0** — no worktree module is redundant |
| PORT | **4** (semantic_cache.py, aider/, voice/, imagegen/) = 7 h |
| REFACTOR | **6** (tiers.py, tier_classifier.py, lead_worker.py, spend_alerts.py, verifier/, escalation_log/) = 19 h |

Key collision: worktree's `TierName` (11 tiers) vs main's `llm_unified.ModelTier` (7 tiers) — must merge before anything downstream can land. Alias conflict: worktree `auto→smart` vs main `auto→fast` would silently double cost on every `model="auto"` call if worktree's version wins unexamined.

### Daemon call-site survey (8 daemons in main repo)

| Daemon | Import sites | generate() calls |
|---|---|---|
| perseus | 3 | 5 |
| titan | 11 | **22** (memory dominates w/ 9 calls) |
| hermes | 6 | 8 |
| clawdbot | 9 | 13 |
| conway | 0 | 0 (no direct LLM calls — only returns tier strings) |
| deerflow_research | 2 | 2 |
| ruflo | 1 | 4 |
| openjarvis | 3 Perseus + 2 Nexa | 3 Perseus + 2 Nexa |
| **TOTAL Perseus path** | **35** | **57** |

**Confirmed P0-2 finding:** **0 matches for `from shared.tiers`** across all 8 daemons. Worktree's `shared/tiers.py` is an orphan module. Every daemon call passes raw string aliases (`"fast"`, `"smart"`, `"genius"`) to `llm.generate(model=...)`.

**2 latent pre-existing bugs discovered during survey** (flagged in PORT-PLAN §5):
1. `clawdbot/a2a_server.py:276` — `tier="fast"` kwarg raises TypeError (main uses `model=`)
2. `openjarvis/core/hooks.py:180` — `from shared.llm_client import ask_llm` (no such symbol in main) raises ImportError

## What Was Produced

**PORT-PLAN.md** at `docs/audits/pre-launch/PORT-PLAN.md`:
- Size: **191 lines** (target ≤ 400)
- All 8 required top-level sections in specified order
- §6 has 6a (single-engineer, 27h), 6b (two-engineer split, ~13h elapsed), 6c (risk-front-loaded reordering)
- §8 lists **7 operator decision questions** (threshold was ≥1)

Effort totals: **4 PORT (7h) + 6 REFACTOR (19h) + 2 latent bug fixes (0.5h) + rebase (0.5h) = 27 hours single-engineer, ~13 hours elapsed for two engineers.**

## Commits

| Task | Commit | Message |
|---|---|---|
| Task 1 | `e9aa314` | chore(launchd): P0-8 normalize 4 Wave R1 daemon plists |
| Task 2 | `606dc34` | docs(audit): Wave R2 — deep-read main repo's shared/llm_client.py |
| Task 3 | `6969dd6` | docs(audit): Wave R2 — classify 10 worktree modules vs main (DELETE/PORT/REFACTOR) |
| Task 4 | `6951c1f` | docs(audit): Wave R2 — survey daemon llm.generate() call sites (8 daemons) |
| Task 5 | `c280b7b` | docs(audit): Wave R2 — write PORT-PLAN.md for Phase 42.5 v2 recovery |

## Deviations from Plan

None — plan executed exactly as written. One minor normalization (n8n plist needed `N8N_HOST` / `N8N_LISTEN_ADDRESS` env vars added for localhost binding compliance) was within Task 1's explicit scope.

## Blockers / Deferred Items

**For operator (morning handoff):** 7 decision questions in PORT-PLAN.md §8. Plans 01-02 and 01-03 cannot proceed with Phase 42.5 v2 recovery execution until the operator picks answers for questions 1 (auto alias), 2 (TierName vs ModelTier name), 3 (Redis dep), and 4 (redactor dedupe). Questions 5, 6, 7 can land as defaults.

**Deferred to Wave R3+:**
- The actual execution of P0-1/P0-2/P0-3 recovery (this plan only produced the PORT-PLAN, operator approval required).
- Fixing the 2 latent daemon bugs (clawdbot `tier=` kwarg, openjarvis `ask_llm` import) — flagged in PORT-PLAN §5 as required edits during REFACTOR pass.

## Self-Check: PASSED

- All 4 plists exist and pass `plutil -lint` ✓
- PORT-PLAN.md exists at expected path ✓
- PORT-PLAN-scratch-llm-client.md (164 L), scratch-diff.md, scratch-daemons.md all exist ✓
- All 5 commits present in git log (e9aa314, 606dc34, 6969dd6, 6951c1f, c280b7b) ✓
- PORT-PLAN.md has all 8 sections + 6a/6b/6c subsections ✓
- PORT-PLAN.md line count 191 ≤ 400 ✓
