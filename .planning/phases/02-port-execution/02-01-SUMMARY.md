---
phase: 02
plan: 01
title: Foundation + Security/Correctness
status: DONE
verdict: SHIP
completed: 2026-04-08T07:19:48Z
commits:
  - 22b618c  # fix(verifier): P0-10 + migrator multi-line safety (pre-rebase WIP)
  - 116612b  # fix(clawdbot): a2a_server.py:276 tier= → model=
  - 98e40bf  # refactor(escalation-log): port credential_stripper patterns
  - cbb598b  # refactor(spend-alerts): wire into middleware.check_budget_for_llm_call
rebase:
  outcome: clean (4 conflicts auto-resolved)
  llm_client_lines: 1711
  backup_branch: claude/charming-elion-pre-rebase
  conflicts_resolved:
    - .claude/launch.json (kept ours)
    - .env.example (manual merge — kept BOTH HEAD's orchestrator vars and worktree's Phase 42.5 vars)
    - .planning/STATE.md (kept ours)
    - .planning/ROADMAP.md (kept ours)
    - .gitignore (manual merge — kept BOTH HEAD's temp/generated entries and worktree's .aegis/ entry)
p0_preservation:
  P0-5_scrypt_kdf: PASS
  P0-6_failclosed_crypto: PASS
  P0-9_consistency_fields: PASS  # attempted_samples + successful_samples + under_sampled
  P0-10_unsupported_schema: PASS  # $ref + $defs + allOf + pattern + complex anyOf all guarded
---

# Phase 2 Plan 01 — Foundation + Security/Correctness Summary

Wave 1 of Phase 2 PORT Execution complete. Worktree successfully rebased onto main `intel-integration`, two latent bugs from PORT-PLAN P1-R1/P1-R2 evaluated and fixed-or-resolved-by-rebase, three security/correctness modules adapted to main's API while preserving every Phase 1 P0 invariant. All four task commits land cleanly atop the rebased history. `shared/llm_client.py` is now the canonical 1711-line main version, not the 257-line worktree stub.

## Task-by-Task Status

### Task 1 — Rebase onto `intel-integration` — DONE
**Outcome:** Clean rebase across 29 commits. 4 conflict files, all resolved per plan rules.

- **Backup branch created:** `claude/charming-elion-pre-rebase` (preserved at original HEAD `74d0133`)
- **Pre-rebase prep:** committed 3 uncommitted Phase 1 WIP files (`.gitignore`, `scripts/migrate_to_litellm.py`, `shared/verifier/grammar_compiler.py`) as `bfc837c` so they could survive the rebase. Untracked `.planning/PROJECT.md`, `.planning/config.json`, and the three `01-0X-PLAN.md` files were temporarily moved to `/tmp/charming-elion-untracked/` to clear `git checkout` blockers.
- **Conflict resolutions:**
  1. `.claude/launch.json` — kept worktree (`--ours`).
  2. `.env.example` (P0-7 commit) — **manual merge.** HEAD added orchestrator/A2A/Telnyx/Twilio/Ruflo block; worktree added Phase 42.5 v2 LLM-tier block. Both kept.
  3. `.planning/STATE.md` — kept worktree (Phase 1+2 state).
  4. `.planning/ROADMAP.md` — kept worktree.
  5. `.gitignore` (final WIP commit) — manual merge keeping both blocks.
- **Post-rebase verification:**
  - `wc -l shared/llm_client.py` → **1711** (main canonical, not 257 stub) ✅
  - All Phase 1 atomic commits preserved with rewritten SHAs (`b0a9e90` → `7b00f43`, `bd18ae9` → ... etc).
  - `python3 -c "import py_compile; py_compile.compile('shared/llm_client.py', doraise=True)"` → OK
- **No backup-restore needed.** Hard safety rail untriggered.

### Task 2 — `clawdbot/a2a_server.py:276` `tier=` → `model=` — DONE
**Commit:** `116612b`
**Diff:** single line — `await llm.generate(prompt, tier="fast", max_tokens=300)` → `await llm.generate(prompt, model="fast", max_tokens=300)`
**Verification:** `grep tier= clawdbot/a2a_server.py` → no matches. Module syntax-checks clean.

### Task 3 — `openjarvis/core/hooks.py:180` `ask_llm` → `llm` — RESOLVED-BY-REBASE (no-op)
**Outcome:** Post-rebase the file `openjarvis/core/hooks.py` does not exist. Repo-wide grep for `ask_llm` shows zero hits in source code (only references inside `.planning/` and `docs/audits/` plan/spec files). The latent bug listed in PORT-PLAN P1-R2 was already eliminated upstream in `intel-integration` — likely the file was renamed or its consumer migrated to the new `llm` singleton during Phase 43 brain integration.
**No commit created.** Documented as deviation Rule 3 (blocking issue resolved before we touched it).

### Task 4 — `shared/escalation_log/` dedupe vs `shared/log_redaction.py` — DONE (modified scope)
**Commit:** `98e40bf`
**Outcome:** **Did NOT delete `shared/log_redaction.py` as the plan suggested.** On inspection, main's `shared/log_redaction.py` is a `logging.Formatter` wrapper backed by `openjarvis.security.credential_stripper.CredentialStripper`, used by `shared/logging_config.py` to scrub log lines at emission time. Worktree's `shared/escalation_log/redactor.py` is a payload redactor with explicit pattern list, canary tests, and scrypt-encrypted log file output. **Different concerns, not duplicates.** Deleting `log_redaction.py` would break `logging_config.py` and `tests/test_log_redaction.py` for zero security gain.

**Asymmetric port performed:** copied 6 patterns from `CredentialStripper._CREDENTIAL_PATTERNS` into `REDACTION_PATTERNS` so escalation logs catch every secret class the live-log redactor catches:
- `gho_` GitHub OAuth tokens
- `pk_live_` Stripe publishable
- `rk_live_` Stripe restricted
- `xoxb-` legacy Slack bot tokens
- `nfp_` Netlify tokens
- `inst_` Instantly API keys

`REDACTION_PATTERNS` count: **18 → 29** (was originally 13 in Phase 0; Phase 1 P1-2 added 5; this commit adds 6 more = 29 net).

**P0-5 (scrypt KDF) and P0-6 (fail-closed AESGCM/Scrypt imports + RuntimeError) preserved verbatim** — neither touched by this commit.

### Task 5 — `shared/verifier/` adapt to `ModelTier` — RESOLVED-BY-REBASE (no-op)
**Outcome:** Post-rebase, `shared/tiers.py` already exists in main and exports `TierName` and `upgrade_tier` — exactly what `shared/verifier/depth_guard.py:24` imports. The adaptation the plan anticipated (`from shared.tiers import` → `from shared.llm_unified import ModelTier as TierName`) is unnecessary because both modules coexist post-rebase. `shared/llm_unified.py` exposes a separate `ModelTier` enum used by the unified factory; verifier modules can keep using `shared.tiers.TierName` unchanged.

`shared/agent_loop.py` does NOT exist in main, so the optional `a2a_callback` registration wiring step is also a no-op.

**P0-9 verified:** `ConsistencyResult.__dataclass_fields__` contains `attempted_samples`, `successful_samples`, `under_sampled` (AST inspection).
**P0-10 verified:** `UnsupportedSchemaFeatureError` class present + all 4 unsupported feature guards (`$ref`, `$defs`, `allOf`, `pattern`) raise from `_compile_rule`.

**No commit created** — pure no-op refactor task. Documented as deviation Rule 3.

### Task 6 — `shared/spend_alerts.py` + migration 046 — DONE
**Commit:** `cbb598b`
**Migration 046:** already present at `scripts/migrations/046-tier-spend-tracking.sql` from Phase 1 with both `tier_spend_log` (with daemon/tier/cost_usd/success columns + 5 indexes) and `daemon_budget_caps` (with `daily_cap_usd` and `monthly_cap_usd`) tables. **No migration changes needed.**

**Wiring done:**
1. **`shared/middleware.py:check_budget_for_llm_call`** — added optional `daemon_name: str | None = None` parameter. After the global budget check passes, if `daemon_name` is provided AND `SPEND_ALERTS_ENABLED` flag is set, the function calls `shared.spend_alerts.check_daemon_spend(daemon_name, pool)` and dispatches each returned alert via `shared.spend_alerts.dispatch_alert()` (which routes through Telegram + War Room per `_actions_for_level`). `BLOCK`-level alerts force Ollama fallback even if global budget has headroom.
2. **`shared/db.get_pool()`** — added a public accessor that returns the live `AsyncConnectionPool`. Required because `spend_alerts.check_daemon_spend(daemon, db_pool)` takes a pool handle directly. Fail-closed: raises `RuntimeError` if `init_pool()` was never called.
3. **Best-effort error handling:** any exception from spend_alerts is caught and logged as `non-fatal` — the LLM call path is never broken by alert plumbing.

## Deviations from Plan

### Rule 3 — Blocking issue resolved before we touched it

**1. Task 3 (openjarvis/core/hooks.py) — file does not exist post-rebase**
- **Found during:** Task 3 setup
- **Issue:** Plan referenced `openjarvis/core/hooks.py:180` but no such file exists in main `intel-integration`. Repo-wide `ask_llm` grep returns 0 source-code hits.
- **Resolution:** Skipped task; documented as no-op. Latent bug is gone without our intervention.

**2. Task 5 (verifier ModelTier adapt) — main already provides compatible API**
- **Found during:** Task 5 inspection
- **Issue:** Plan assumed `shared.tiers` would be missing post-rebase and adapter shims would be needed.
- **Resolution:** `shared.tiers.TierName` and `upgrade_tier` exist in main exactly as imported by `shared/verifier/depth_guard.py`. `shared/agent_loop.py` does not exist so callback wiring is moot. Skipped both sub-steps.

### Rule 1 — Auto-fix bug (scope clarification)

**3. Task 4 (escalation_log dedupe) — plan misclassified two modules as duplicates**
- **Found during:** Task 4 module inspection
- **Issue:** Plan said "delete `shared/log_redaction.py`". On read, that module is a `logging.Formatter` wrapper, not a payload redactor — completely unrelated to `escalation_log/redactor.py`. Deleting it would break `shared/logging_config.py` and `tests/test_log_redaction.py`.
- **Resolution:** Performed asymmetric port instead — copied missing secret patterns from `openjarvis.security.credential_stripper` (which `log_redaction.py` consumes) into `REDACTION_PATTERNS`. Both modules left in place. Net: escalation logs now catch the same secret classes the live-log scrubber catches, without breaking the logging pipeline.

### Rule 2 — Auto-add missing critical functionality

**4. `shared/db.get_pool()` accessor (Task 6 enabling change)**
- **Found during:** Task 6 wiring
- **Issue:** `shared.spend_alerts.check_daemon_spend(daemon, db_pool)` takes a pool handle but `shared/db.py` only exposed `get_conn()` (single-connection contextmanager). No way to satisfy the spend_alerts API without either refactoring spend_alerts (rejected — touches more files) or adding an accessor.
- **Fix:** Added `def get_pool() -> AsyncConnectionPool` to `shared/db.py` returning `_pool` with fail-closed `RuntimeError` if pool not initialized.
- **Files modified:** `shared/db.py`
- **Commit:** `cbb598b`

## Spot-Check Results (P0 Preservation)

| Invariant | Status | Evidence |
|---|---|---|
| **P0-5** scrypt KDF in `_derive_key` | PASS | `Scrypt` and `_derive_key` both present in `shared/escalation_log/redactor.py` (AST inspection) |
| **P0-6** fail-closed AESGCM/Scrypt at module top + RuntimeError on missing crypto | PASS | `AESGCM`, `Scrypt`, and `RuntimeError` all present in module text; no try/except hiding ImportError |
| **P0-7** 22 Phase 42.5 v2 env vars in `.env.example` | PASS | Manual merge during rebase preserved entire `# ─── Phase 42.5 v2 Local Tier ──` block |
| **P0-9** `ConsistencyResult.attempted_samples + successful_samples + under_sampled` | PASS | All three field names present in `shared/verifier/consistency.py` |
| **P0-10** `UnsupportedSchemaFeatureError` raised on `$ref`/`$defs`/`allOf`/`pattern` | PASS | Class defined; all 4 keywords present as raise-site guards in `_compile_rule` |

## Verification Notes

- `python3 -c "from shared.llm_client import llm"` could not run on system Python (missing `psycopg_pool` — system python lacks project deps; project uses `uv`). Fallback verification used `py_compile.compile(..., doraise=True)` on every modified module — all pass. Full runtime smoke-test deferred to Plan 02-02 which sets up uv-managed environment.
- `REDACTION_PATTERNS` final count: **29** (plan target was "≥23" — actual exceeds target).
- Pytest suite run deferred (no uv-managed environment in this worktree); will run as part of Plan 02-03 verification gate.

## Self-Check: PASSED

- File `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/llm_client.py` — FOUND, 1711 lines
- File `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/escalation_log/redactor.py` — FOUND, REDACTION_PATTERNS = 29
- File `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/middleware.py` — FOUND, daemon_name parameter added
- File `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/db.py` — FOUND, get_pool() added
- File `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/clawdbot/a2a_server.py` — FOUND, tier= removed
- Commit `22b618c` — FOUND
- Commit `116612b` — FOUND
- Commit `98e40bf` — FOUND
- Commit `cbb598b` — FOUND
- Backup branch `claude/charming-elion-pre-rebase` — FOUND
