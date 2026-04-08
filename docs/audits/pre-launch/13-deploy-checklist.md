# Deploy Checklist — Phase 42.5 v2 (Mac Studio M4 Max)

**Date:** 2026-04-07
**Deployer:** Operator (Majo)
**Target:** Mac Studio M4 Max (14C CPU / 32C GPU / 36GB / 512GB)
**Codebase:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/`
**Audit depth:** Overkill (operator requested)

---

## Summary Scorecard

| Category | Items | Pass | Fail | N/A |
|----------|-------|------|------|-----|
| Migrations | 6 | 5 | 1 | 0 |
| Healthchecks | 14 | 2 | 12 | 0 |
| Env vars | 15 | 0 | 15 | 0 |
| launchd plists | 10 | 6 | 4 | 0 |
| Logging | 4 | 2 | 2 | 0 |
| Runbooks | 6 | 6 | 0 | 0 |
| Git hygiene | 3 | 0 | 3 | 0 |
| Tests | 8 | 5 | 3 | 0 |
| Sandbox profile | 3 | 1 | 2 | 0 |
| Migrate dry-run | 3 | 3 | 0 | 0 |
| Cutover approval | 3 | 1 | 2 | 0 |
| **TOTAL** | **75** | **31** | **42** | **2** |

**Deploy verdict: BLOCKED.** 42 items fail or require fixes. Do not flip cutover until env vars are documented, remaining launchd plists created, and git status clean.

---

## 1. Migrations (046, 047)

- [x] `scripts/migrations/046-tier-spend-tracking.sql` wrapped in `BEGIN;`/`COMMIT;` (atomic)
- [x] 046 uses `CREATE TABLE IF NOT EXISTS` for all 3 tables (tier_spend_log, daemon_budget_caps, daily_spend_summary)
- [x] 046 seed `INSERT ... ON CONFLICT (daemon) DO NOTHING` — idempotent re-runs safe
- [x] 046 uses `CREATE INDEX IF NOT EXISTS` for all 5 indexes
- [x] 047 `scripts/migrations/047-shadow-diffs.sql` uses `CREATE TABLE IF NOT EXISTS` + indexes with `IF NOT EXISTS`
- [ ] **FAIL:** Neither migration is tested against a populated dev DB rollback (no `./scripts/test-migration.sh` run recorded). Must run `psql -v ON_ERROR_STOP=1 -f 046...` twice against a clone and confirm second run is a no-op.

## 2. Healthchecks — 8 daemons + native services

- [x] `perseus` daemon healthcheck wired (plist present)
- [x] `titan` daemon healthcheck wired (plist present)
- [ ] **FAIL:** `hermes` — no dedicated plist found; running via master?
- [ ] **FAIL:** `clawdbot` plist present but no `/health` endpoint audit
- [ ] **FAIL:** `conway` — no plist, no healthcheck
- [ ] **FAIL:** `deerflow` — no plist, no healthcheck
- [ ] **FAIL:** `ruflo` — no plist, no healthcheck
- [ ] **FAIL:** `openjarvis` — no plist, no healthcheck
- [ ] **FAIL:** LiteLLM proxy — no `/health/liveliness` probe configured in monitoring
- [ ] **FAIL:** MLX server (Qwen3-30B-A3B) — no healthcheck shell script
- [ ] **FAIL:** Parakeet (STT) — `com.perseus.parakeet.plist` MISSING
- [ ] **FAIL:** Kokoro (TTS) — `com.perseus.kokoro.plist` MISSING
- [ ] **FAIL:** Draw Things — no plist, no API probe
- [ ] **FAIL:** mem0 — `com.perseus.mem0.plist` MISSING

## 3. Environment Variables (.env.example documentation)

Searched `.env.example` (105 lines). **ALL 15 Phase 42.5 v2 env vars are MISSING:**

- [ ] **FAIL:** `CONWAY_KEYSTORE_PASSWORD` — not in .env.example
- [ ] **FAIL:** `LITELLM_MASTER_KEY` — not in .env.example
- [ ] **FAIL:** `OPENROUTER_API_KEY` — not in .env.example
- [x] `RECRAFT_API_KEY` — **present** (line 57), but listed as FAIL in task — CORRECTION: PASS
- [ ] **FAIL:** `AIRLLM_LOCAL_KEY` — not in .env.example
- [ ] **FAIL:** `GEMINI_API_KEY` — not in .env.example
- [ ] **FAIL:** `DEEPSEEK_API_KEY` — not in .env.example
- [ ] **FAIL:** `LANGFUSE_HOST` — not in .env.example
- [ ] **FAIL:** `LANGFUSE_PUBLIC_KEY` — not in .env.example
- [ ] **FAIL:** `LANGFUSE_SECRET_KEY` — not in .env.example
- [ ] **FAIL:** `CLICKHOUSE_PASSWORD` — not in .env.example
- [ ] **FAIL:** `LITELLM_SALT_KEY` — not in .env.example
- [ ] **FAIL:** `PARAKEET_API_BASE` — not in .env.example
- [ ] **FAIL:** `KOKORO_API_BASE` — not in .env.example
- [ ] **FAIL:** `DRAW_THINGS_API_BASE` — not in .env.example

**Action:** Extend `.env.example` with a `# ── Phase 42.5 v2 (Local Tier) ──` block before cutover.

## 4. launchd plists

Found in `scripts/launchagents/`:

- [x] `com.perseus.master.plist`
- [x] `com.perseus.titan.plist`
- [x] `com.perseus.clawdbot.plist`
- [x] `com.perseus.frontend.plist`
- [x] `com.perseus.dashboard.plist`
- [x] `com.perseus.backup.plist`
- [ ] **FAIL:** `com.perseus.parakeet.plist` — NOT FOUND
- [ ] **FAIL:** `com.perseus.kokoro.plist` — NOT FOUND
- [ ] **FAIL:** `com.perseus.mem0.plist` — NOT FOUND
- [ ] **FAIL:** `com.perseus.n8n.plist` — NOT FOUND

## 5. Logging — tier_spend_log writes

- [x] Schema defined in 046 migration (36 fields)
- [x] `daily_spend_by_tier` view created for War Room dashboard
- [ ] **FAIL:** No verified writer code path found wired from LiteLLM callbacks → `tier_spend_log` (needs grep of `tier_spend_log` INSERT statements in litellm callbacks)
- [ ] **FAIL:** No log rotation or retention policy documented for `tier_spend_log` (will grow unbounded)

## 6. Runbooks

All present in `docs/runbooks/`:

- [x] `cutover-playbook.md`
- [x] `local-tier-rollback.md`
- [x] `native-services-setup.md`
- [x] `image-gen-routing.md`
- [x] `voice-loop-guide.md`
- [x] `aider-pattern-guide.md`

## 7. Git Status

- [ ] **FAIL:** `M .gitignore` — modified but uncommitted
- [ ] **FAIL:** 11 untracked audit reports in `docs/audits/pre-launch/` (01-igus, 02-aegis, 03-cso, 06-misalignment, 07-paul-audit, 08-plan-eng-review, 09-quality-gate, 10-simplify, 11-tech-debt, 17-base-audit-claude, 02-aegis-summary)
- [ ] **FAIL:** Working tree NOT clean. Must commit or stash before deploy.

## 8. Test Files — Phase 42.5 relevance

- [x] `tests/test_phase40_litellm_backend.py` exists
- [x] `tests/test_phase41_tiers.py` exists
- [x] `tests/test_phase42_semantic_cache.py` exists
- [x] `tests/test_phase43_classifier.py` + `test_phase43_lead_worker.py`
- [x] `tests/test_phase44_regression.py` + `test_verifier_layers.py`
- [ ] **FAIL:** No `test_phase42_5_*.py` for local-tier, MLX routing, Parakeet/Kokoro adapters
- [ ] **FAIL:** `pytest --collect-only` not actually executed (cannot verify importability without a venv run)
- [ ] **FAIL:** No test for 046/047 migration idempotency

## 9. Sandbox profile

- [x] `litellm/sandboxes/verifier.sb` present
- [ ] **FAIL:** No Python wrapper found that loads `verifier.sb` via `sandbox-exec -f verifier.sb` for verifier layer invocations
- [ ] **FAIL:** No test confirming sandbox denies network/FS outside whitelist

## 10. migrate_to_litellm.py --dry-run

- [x] `scripts/migrate_to_litellm.py` present
- [x] `--dry-run` flag implemented (line 279) and documented in docstring (lines 25-26)
- [x] `--apply`, `--path`, `--report` supporting flags for targeted rollout

## 11. Cutover playbook approval

- [x] `docs/runbooks/cutover-playbook.md` exists
- [ ] **FAIL:** No sign-off record in repo (e.g. `APPROVED-BY: operator` frontmatter or audit log)
- [ ] **FAIL:** Playbook has not been walked through end-to-end against this specific machine (M4 Max, not M3 Ultra)

---

## Top 5 Failed Items (blockers)

1. **15 Phase 42.5 env vars missing from `.env.example`.** Anyone cloning the repo has zero chance of booting the local tier. Fix BEFORE cutover.
2. **4 launchd plists missing** (`parakeet`, `kokoro`, `mem0`, `n8n`). Native services will not auto-start on reboot; the whole voice loop and memory backend die on first crash.
3. **6 of 8 daemons have no individual healthcheck/plist** (hermes, conway, deerflow, ruflo, openjarvis, clawdbot-health). Master plist bundles some but is opaque; needs per-daemon liveness.
4. **Working tree dirty** — 11 untracked audit files + modified `.gitignore`. A cutover from a dirty tree is unrollback-able cleanly.
5. **Logging path from LiteLLM → `tier_spend_log` not verified.** Migration creates the table but no confirmed writer means budget enforcement is blind on day 1.

---

## Rollback Triggers

Roll back (`scripts/rollback_litellm.py`) if:
- Any daemon fails to start within 5 min of cutover
- `tier_spend_log` receives zero writes in first 30 min
- Verifier layer L1-L4 failure rate > 15% over any 15-min window
- Parakeet STT or Kokoro TTS latency p95 > 5s
- MLX server OOM on Qwen3-30B-A3B load (36GB RAM ceiling)
- Any cost overrun vs shadow-diff baseline > 2×

## Sign-off

- [ ] Operator explicit "APPROVED FOR CUTOVER" recorded
- [ ] All 42 failed items resolved OR consciously waived with rationale
- [ ] Dry-run of `migrate_to_litellm.py --dry-run` executed and report reviewed
- [ ] 48h shadow-diff window completed with similarity > 0.85

**Report path:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/docs/audits/pre-launch/13-deploy-checklist.md`
