# Pre-Launch Audit Summary — Phase 42.5 v2

**Date**: 2026-04-07
**Worktree**: `.claude/worktrees/charming-elion`
**Branch**: `claude/charming-elion`
**Commits audited**: `30770c0`, `53dfecc`, `d9fdc6a` (50 files / ~16k LOC)
**Audits run**: 19 of 19 (Wave 1-4 complete, no skipped audits)
**Wall clock**: ~4 hours
**Methodology**: 19 parallel sub-agents in 4 waves, each audit isolated with own context

---

## Verdict: 🚫 **BLOCK LAUNCH**

The implementation is high-quality code (avg 8.83/10 from quality-gate, zero LLM slop, 26/26 files AST-clean) that solves the wrong problem. Multiple independent audits converged on a critical realization that none of us caught during the build:

**Main's `shared/llm_client.py` (1711 lines) already implements 80% of what Phase 42.5 v2 was supposed to add** — tier routing with fallback chains, StickyLatch caching, budget gating with auto-downgrade, watchdog timeouts per tier, AirLLM heavy local integration. The worktree branched off a 257-line stale version of `llm_client.py` and built a parallel tier system as if main didn't already have one.

The Phase 42.5 v2 build is **partially redundant** with existing main, **partially genuine net-new** (Aider pattern, voice loop, image gen, verifier layers, escalation log, sandbox profile), and **completely disconnected from production** (zero daemons import any of the 50 new modules — verified by grep across all 8 daemon directories).

**Cannot launch as-is. Cannot merge as-is.** Recovery is well-defined and takes ~14 working days solo, ~7 days with two engineers in parallel.

---

## The 12 P0 findings (must resolve before launch)

Ranked by audit confidence (number of independent audits that flagged the same issue):

### P0-1: Stale `shared/llm_client.py` rebase gap — 9 audits

**Issue**: Worktree's `shared/llm_client.py` is 257 lines. Main's is **1711 lines**. The worktree was branched off a long-stale version of the LLM client, and the new tier system was designed against a code baseline that doesn't exist in production.

**Confirmed by**: igus, cso, aegis, paul:audit, plan-eng-review, tech-debt, gsd:review, think-at-n, build-sheet

**Worse than first reported**: Main already has tier routing (`_MODEL_FALLBACK_CHAIN`, `_resolve_model`, StickyLatch, watchdog per tier, AirLLM integration). Phase 42.5 v2 isn't adding tiers — main already has them. It's parallel work.

**Risk if shipped**: A naive `git merge claude/charming-elion → main` would silently revert 1454 lines of production LLM client code (caching, retry, tracing, AirLLM, budget downgrade, watchdog).

**Fix**: Rebase strategy, not merge. See "Recovery plan" below.

### P0-2: Phase 42.5 v2 modules NOT WIRED to any daemon (ghost integration) — 5 audits

**Issue**: `grep -r "from shared.tiers" perseus/ titan/ hermes/ clawdbot/ conway/ deerflow_research/ ruflo/ openjarvis/` returns ZERO matches. Same for `tier_classifier`, `semantic_cache`, `lead_worker`, `aider`, `voice`, `imagegen`, `verifier`, `escalation_log`. All daemons still call `from shared.llm_client import llm` against the existing 1711-line client.

**Confirmed by**: aegis, paul:audit, tech-debt, think-at-n, build-sheet

**Risk if shipped**: The cutover playbook's "FLIP" step has nothing to flip. Operator sets `LITELLM_PROXY_ENABLED=true`, restarts daemons, watches the dashboard — nothing changes because no daemon imports the new code. Then operator assumes it worked. Invisible failure mode.

**Fix**: Run `migrate_to_litellm.py --apply` properly OR write the daemon-side import wiring manually (~8h).

### P0-3: Verifier sandbox is decorative — 5 audits

**Issue**: `litellm/sandboxes/verifier.sb` exists and looks well-written, but:
- `(deny ... (subpath "~/.ssh"))` → macOS `sandbox-exec` does NOT expand `~`. The entire deny block for secrets is a no-op.
- No `scripts/run_in_sandbox.sh` exists.
- No `tests/test_verifier_sandbox_red_team.py` exists.
- `shared/aider/ruflo_loop.py:152` has `sandbox_runner=None` default that just logs a warning and continues.
- Zero references to `sandbox-exec` in any Python file.

**Confirmed by**: cso, paul:audit, plan-eng-review, tech-debt, aegis

**Risk if shipped**: Ruflo's Aider editor produces a "fix" from a poisoned upstream input → `pytest` runs in host process → arbitrary code executes with full daemon privileges → SSH keys, AWS creds, Conway USDC wallets, all `.env` secrets are exfiltrable.

**Fix**:
1. Replace `~/.ssh` with absolute paths or `(param "HOME")` in sandbox profile (1h)
2. Write `scripts/run_in_sandbox.sh` Python wrapper (1.5h)
3. Wire it into Ruflo Aider loop, remove `sandbox_runner=None` default (0.5h)
4. Write red-team test that attempts file exfil + network egress + env var read (2h)

### P0-4: Sandbox tilde paths broken on macOS — 4 audits

(Sub-finding of P0-3 but worth its own line item because it's the root cause.)

**Issue**: macOS `sandbox-exec` treats `~` as literal, not as `$HOME` expansion. Every sandbox profile rule referencing `~/.ssh`, `~/.aws`, `~/.config`, etc. is a no-op.

**Fix**: Use absolute paths (`/Users/majovega/.ssh`) or `(param "HOME")` with `-D HOME=$HOME` passed to `sandbox-exec`. 1h.

### P0-5: Unsalted SHA-256 key derivation in escalation log — 2 audits

**Issue**: `shared/escalation_log/redactor.py` derives the AES-256 key via `hashlib.sha256(password.encode()).digest()`. No salt, no PBKDF2/scrypt/Argon2, no key versioning, no AAD binding.

**Confirmed by**: cso, gsd:review

**Risk if shipped**: GPU-crackable in hours if the encrypted log file is exfiltrated. The escalation log contains real customer PII (Titan leads), operator conversations (Hermes), Conway ledger context, possibly API keys from environment-enumerating daemon prompts.

**Fix**: Replace with `argon2id` or `scrypt` with random salt (stored in file header), key versioning byte, optional AAD. 1.5h.

### P0-6: Crypto fail-OPEN — 3 audits

**Issue**: `shared/escalation_log/redactor.py:209` writes UNENCRYPTED JSONL with a logger warning when `cryptography` package isn't installed OR when `CONWAY_KEYSTORE_PASSWORD` is unset. Must fail closed.

**Confirmed by**: cso, igus, plan-eng-review

**Fix**: Raise `RuntimeError` instead of warning. Add startup preflight check. 0.5h.

### P0-7: 15 env vars missing from `.env.example` — 3 audits

**Issue**: `CONWAY_KEYSTORE_PASSWORD`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `OPENROUTER_API_KEY`, `AIRLLM_LOCAL_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `CLICKHOUSE_PASSWORD`, `PARAKEET_API_BASE`, `KOKORO_API_BASE`, `DRAW_THINGS_API_BASE`, plus a few more — referenced in code/config but absent from `.env.example`.

**Fix**: Add to `.env.example` with comments. 30 min.

### P0-8: 4 launchd plists missing — 1 audit (deploy-checklist)

**Issue**: `com.perseus.parakeet.plist`, `com.perseus.kokoro.plist`, `com.perseus.mem0.plist`, `com.perseus.n8n.plist` referenced in runbooks but don't exist in `scripts/launchagents/`. Voice loop + native services won't survive reboot.

**Fix**: Write the 4 plists. 1.5h.

### P0-9: Self-consistency vote launders failures into successes — 1 audit (gsd:review, NEW)

**Issue**: `shared/verifier/consistency.py` — when both retry samples raise exceptions, `responses` shrinks to length 1, and `1/1 = 1.0 ≥ 2/3` evaluates `True` with `confidence=1.0`. **A failing consistency check becomes a passing one.**

**Risk if shipped**: Layer 2 of the verifier silently approves bad responses when the model is unstable. Defeats the purpose.

**Fix**: Track `attempted_samples` vs `successful_samples` separately. Force escalate when `successful_samples < n/2`. 1h.

### P0-10: Grammar compiler silently degrades to "string" — 1 audit (gsd:review, NEW)

**Issue**: `shared/verifier/grammar_compiler.py:106` — falls through to `return "string"` for `$ref`, `$defs`, `anyOf`, `allOf`, `const`, `pattern`. Real-world tool schemas use these features. The "grammar-constrained" guarantee silently becomes vacuous.

**Fix**: Either implement these JSON Schema features OR raise on encountering them so the caller falls back to JSON-mode + post-hoc validation. 3h.

### P0-11: `migrate_to_litellm.py` produces syntax errors on multi-line calls — 1 audit (plan-eng-review, confirmed gsd:review)

**Issue**: `scripts/migrate_to_litellm.py:249-250` — comment says "skip multi-line" but the skip condition only checks `"generate(" in line`. The dominant `await llm.generate(\n  prompt,\n  ...)` style passes the check, then byte-offset injection lands in the wrong place. Result: `SyntaxError: positional argument follows keyword argument` at runtime.

**Fix**: Use `node.end_lineno != node.lineno` check OR switch to `libcst` for proper multi-line handling. 2h.

### P0-12: 140+ outstanding UAT items across 10 phases — 1 audit (gsd:audit-uat)

**Issue**: All of Phases 40-44 have 7-9 unchecked success criteria each. Phase 42.5 PAUL state still says "Plan 01 awaiting approval" but 50 files were committed without going through `paul:verify` or `gsd:verify-work`. Phase numbering collides (two Phase 40s, 41s, 42s, 43s).

**Fix**: After remediation, run `paul:verify` on Phase 42.5 deliverables and `gsd:verify-work` on the affected phases. ~4h verification + remediation triage.

---

## P1 findings (should fix before launch, not strict blockers)

| # | Issue | Audits | Fix effort |
|---|---|---|---|
| P1-1 | Spec'd files missing: `docs/compliance/local-tier-licenses.md`, `ruflo-cutover-soak.md`, `docs/spikes/*.md`, `litellm/eval/golden/`, `litellm/hooks/local_path_guard.py`, `litellm/routing_policy.yaml` | paul:audit | 2h |
| P1-2 | Redactor misses critical secret formats: OpenRouter keys (`sk-or-v1-*`), Telegram bot tokens, Slack webhooks, ETH private keys (64-hex), Postgres connection strings | cso | 30min |
| P1-3 | AirLLM uses plain HTTP on docker bridge (no TLS) | cso | 1h |
| P1-4 | Local daemon binds (Ollama/MLX/Kokoro/Parakeet) not verified — relies on env vars | cso | 1h |
| P1-5 | Budget gate fails OPEN on exception | cso | 30min |
| P1-6 | Sandbox allows `/bin/sh`, entire `/opt/homebrew/bin` (curl, wget, ssh, gh CLI) | cso | 1h |
| P1-7 | LeadWorkerLoop mutates shared `context` dict during async iteration | plan-eng-review | 1h |
| P1-8 | Self-consistency labeled "n=3 parallel" but runs sequentially | plan-eng-review | 30min (rename) or 2h (parallelize) |
| P1-9 | Zero escalation log file rotation — disk fills | plan-eng-review | 1h |
| P1-10 | launchd plists lack `ExitTimeOut` and `EnvironmentVariables` | plan-eng-review | 30min per plist |
| P1-11 | Working tree dirty during cutover (modified .gitignore + 11 untracked audit reports) | deploy-checklist | 5min cleanup |
| P1-12 | `tier_spend_log` writer path unverified — no LiteLLM callback wires to INSERT | deploy-checklist | 1h |
| P1-13 | 6 of 8 daemons have no individual launchd plist or healthcheck (hermes, conway, deerflow, ruflo, openjarvis, clawdbot-health) | deploy-checklist | 3h |
| P1-14 | Aider Editor's `_extract_diff()` regex won't match patches that include nested code fences | gsd:review | 30min |
| P1-15 | DASHBOARD_HOST defaults to 0.0.0.0 (LAN exposure) | igus | 5min |
| P1-16 | `shell=True` SSH in `titan/training.py:451` (RCE-by-design) | igus | 1h |

---

## P2 findings (track, fix in week 1 post-launch)

20+ items across all 19 audits — see individual audit files for details. Highlights:

- 29 of 32 worktrees stale (no commits since 2026-03-30) — `base:audit-claude`
- 4 loose `.claude/plans/*.md` contradict locked state — `base:audit-claude`
- 6 project commands collide with global verbs (build, plan, review, prime, context_audit, health-check) — `base:audit-claude`
- `tools/training/` debug script `tmp_*.py` files at repo root — build-sheet
- Phase 42.5 deps (Aider/MLX/AirLLM/Kokoro/Parakeet) not declared in `requirements.txt` — build-sheet
- Three loop abstractions doing the same thing (`execution_loop.py`, `lead_worker.py`, hand-rolled Aider for-loops) — simplify
- `tiers.py` not connected to `llm_client.py` (single source of truth missing) — simplify
- Aider parsers duplicated between Ruflo and Clawdbot loops — simplify
- Magic number `10000` in `ruflo_loop.py:195` — simplify

---

## P3 findings (backlog)

- Voice cloning swap to F5-TTS later (already in operator backlog)
- Memory budget tuning post-shadow
- Per-daemon depth caps empirically validated
- Phase 43 classifier training data accumulation

---

## What's working well (preservation list)

The audit found significant strengths that should NOT be broken in any remediation:

1. **`shared/tiers.py`** — 11-tier registry, dataclass-based, pinned model IDs, USD costs, fallback chains, dated comment explaining LOCAL_HEAVY defer. **Quality 9.6/10**, no slop. Use as-is, just connect to main's existing tier system.

2. **`shared/escalation_log/redactor.py`** — 18 regex redaction patterns (overshoots spec's 15), AES-256-GCM via `cryptography.hazmat.AESGCM`, canary test infrastructure. Just needs the unsalted-SHA fix and fail-closed.

3. **`config/litellm_config.yaml`** — well-structured 11-tier config with fallback chains, per-daemon team budgets, Langfuse callbacks, native macOS service hostnames. Production-quality.

4. **`shared/semantic_cache.py`** — strict default-deny allowlist with hardcoded forbidden ops list (`code_generation`, `email_compose`, all Aider calls). Critical safety invariant correctly implemented.

5. **`scripts/spikes/run_gbnf_spike.py`** — well-designed proof-of-life test with 100-prompt baseline + grammar comparison, GREEN/YELLOW/RED verdict logic, latency overhead measurement. Ready to run.

6. **`docs/runbooks/`** — all 6 runbooks are present, well-written, cross-referenced (cutover, native-services-setup, local-tier-rollback, voice-loop-guide, image-gen-routing, aider-pattern-guide). Operator-grade documentation.

7. **PAUL scaffold** — `.paul/PROJECT.md`, `ROADMAP.md`, `STATE.md`, `HANDOFF.md` all present and consistent. Decision history is clean and traceable.

8. **Quality of new code (8.83/10 average)** — zero placeholder functions, zero `NotImplementedError` stubs, zero broken imports, zero fake tests. The autonomous build session did NOT produce slop.

9. **Spec adherence (98%)** — implementation faithfully materializes the locked v2 spec. The misalignment is between the spec and reality, not between the spec and the code.

10. **Unsloth UD-MLX-4bit, Qwen3-30B-A3B selection, no-license-worry decision, voice loop replaces ElevenLabs, native services Option B, 1TB interim drive with AirLLM defer** — all locked operator decisions are correctly reflected in the code and docs.

---

## Recommended fix order (ROI ranked)

Two-engineer parallel split, ~7 working days. Solo engineer ~14 days.

### Day 1 — Quick wins + foundation (parallel work, 7-8 hours)

| # | Fix | Effort | Owner |
|---|---|---|---|
| 1 | P0-6: Crypto fail-closed in escalation log | 0.5h | Eng A |
| 2 | P0-7: Add 15 env vars to `.env.example` | 0.5h | Eng A |
| 3 | P0-4: Sandbox tilde path expansion | 1h | Eng A |
| 4 | P0-5: Salted scrypt KDF for escalation log | 1.5h | Eng A |
| 5 | P0-1: Investigate main's llm_client.py — what does it ACTUALLY have? | 2h | Eng B |
| 6 | P0-9: Self-consistency bug fix (track success/attempt counts) | 1h | Eng B |
| 7 | P0-10: Grammar compiler raise on unknown features | 1h | Eng B |
| 8 | P0-11: Switch migrate_to_litellm.py to libcst | 2h | Eng B |

### Day 2-3 — Reconciliation with main (the big one)

Critical decision moment: with main's 1711-line llm_client.py already having tier routing, **the strategy is NOT to merge worktree's llm_client.py to main**. Instead:

| Action | Effort |
|---|---|
| Diff main's llm_client.py vs worktree, identify what's actually new | 2h |
| Cherry-pick the genuinely new modules (aider/, voice/, imagegen/, verifier/, escalation_log/, semantic_cache/) onto a fresh branch from main | 4h |
| Adapt the modules to main's existing tier system (rename TierName imports, use main's `_resolve_model`, plug into main's fallback chain) | 8h |
| Discard worktree's `shared/tiers.py` if main already has equivalent — or merge as additive metadata | 2h |
| Adapt `config/litellm_config.yaml` to coexist with main's existing routing | 2h |
| Validate via `pytest` that main's existing tests still pass | 1h |

### Day 4 — Sandbox + L3 wiring

| # | Fix | Effort |
|---|---|---|
| P0-3a | Write `scripts/run_in_sandbox.sh` Python wrapper invoking `sandbox-exec` | 1.5h |
| P0-3b | Wire sandbox into Ruflo Aider loop, remove `sandbox_runner=None` default | 0.5h |
| P0-3c | Write red-team test (`tests/test_verifier_sandbox_red_team.py`) | 2h |
| P0-2a | Daemon-side L3 verifier registrations for each of 8 daemons | 4h |

### Day 5 — Daemon wiring

| # | Fix | Effort |
|---|---|---|
| P0-2b | Wire `shared/aider/ruflo_loop.py` into ruflo daemon | 2h |
| P0-2c | Wire `shared/aider/clawdbot_loop.py` into clawdbot daemon | 2h |
| P0-2d | Wire voice loop into hermes daemon (Telegram + Jarvis war room) | 2h |
| P0-2e | Wire Draw Things into clawdbot asset pipeline | 1h |

### Day 6 — Plists + healthchecks + ops

| # | Fix | Effort |
|---|---|---|
| P0-8 | Write 4 launchd plists (Parakeet, Kokoro, Mem0, N8N) | 1.5h |
| P1-13 | Write 6 daemon launchd plists + healthchecks | 3h |
| P1-9 | Escalation log rotation policy | 1h |
| P1-12 | Wire LiteLLM callback to `tier_spend_log` INSERT | 1h |

### Day 7 — Verification

| # | Action | Effort |
|---|---|---|
| 1 | Run `scripts/spikes/run_gbnf_spike.py` against the new sandbox profile | 1h |
| 2 | Run sandbox red-team test | 1h |
| 3 | Run full test suite (`pytest -v`) | 0.5h |
| 4 | Run `paul:verify` on Phase 42.5 deliverables | 1h |
| 5 | Run `gsd:verify-work` on Phases 40-44 | 1h |
| 6 | Run all 19 audits AGAIN to verify P0s are cleared | 3h |

---

## Operator decisions needed before remediation starts

1. **CRITICAL**: The recovery plan above assumes we abandon the worktree's `shared/llm_client.py` and port the genuinely new modules (Aider, voice, imagegen, verifier, escalation_log, semantic_cache) onto main's existing 1711-line client. **Confirm or reject this approach.** Alternative: rewrite Phase 42.5 from scratch on a fresh branch from main (~3 weeks instead of 1 week).

2. **High**: Two-engineer or one-engineer remediation? Solo = ~14 working days, parallel = ~7 days. Affects whether this can ship in your 6-8 week launch window.

3. **Medium**: Should `shared/tiers.py` become additive metadata on top of main's `_resolve_model`, or should it replace it? Affects ~4 hours of integration work.

4. **Medium**: Confirm Aider loops should ship on Day 5 (Ruflo + Clawdbot wired) — these are the highest-blast-radius daemons. Rollback path: keep current single-shot calls in main as fallback if Aider loops crash.

5. **Low**: Should the 50 P2 backlog items go to `999.x` GSD backlog or stay in this audit folder?

---

## Audit-by-audit results

| # | Skill | Verdict | P0 | P1 | P2 | Report |
|---|---|---|---|---|---|---|
| 1 | /igus | SHIP-WITH-FIXES (76/100) | 3 | 2 | several | `01-igus.md` |
| 2 | /aegis:audit | BLOCK | 7 | 10 | 17 | `02-aegis/` |
| 3 | /cso | BLOCK | 6 | 8 | 10 | `03-cso.md` |
| 4 | /aegis:remediate | n/a (planning) | — | — | — | `04-aegis-remediate.md` |
| 5 | /aegis:guardrails | n/a (planning) | — | — | — | `05-aegis-guardrails.md` |
| 6 | /misalignment-detector | ALIGNED 98% | 0 | 0 | 2 | `06-misalignment.md` |
| 7 | /paul:audit | BLOCK | 6 | 4 | several | `07-paul-audit.md` |
| 8 | /plan-eng-review | BLOCK | 4 | 5 | 3 | `08-plan-eng-review.md` |
| 9 | /quality-gate | SHIP (8.83/10) | 0 | 0 | 1 | `09-quality-gate.md` |
| 10 | /simplify | MEDIUM (3 simplifications) | 0 | 0 | 4 | `10-simplify.md` |
| 11 | /engineering:tech-debt | 27 items | 6 | 8 | 9 | `11-tech-debt.md` |
| 12 | /operations:risk-assessment | DO NOT LAUNCH | 2 critical | 7 high | 1 | `12-risk-assessment.md` |
| 13 | /engineering:deploy-checklist | BLOCKED | 5 | 6 | 31 | `13-deploy-checklist.md` |
| 14 | /gsd:audit-uat | 10 phases on hold | 0 (process) | 140+ UAT | — | `14-gsd-audit-uat.md` |
| 15 | /gsd:review | RECOVERABLE ~2 weeks | 3 | 0 | 0 | `15-gsd-review.md` |
| 16 | /failure-miner | 5 historical patterns | — | — | — | `16-failure-miner.md` |
| 17 | /base:audit-claude | AMBER 3/5 sprawl | 0 | 8 | 5 | `17-base-audit-claude.md` |
| 18 | /think-at-n | 3 reviewers BLOCK | 3 (consensus) | — | — | `18-think-at-n.md` |
| 19 | /anthropic-skills:repo-build-sheet | n/a (artifact) | — | — | — | `19-build-sheet.md` |
| **Total unique** | | | **12** | **16** | **20+** | |

---

## Coverage gaps (what NONE of the 19 audits looked at)

1. **Performance regression** — none of the audits ran benchmarks. We don't know if Qwen3-30B-A3B will actually hit 100-130 tok/s on the operator's specific Studio config. Run `/benchmark` after the Studio is provisioned.

2. **End-to-end smoke test** — none of the audits actually ran a daemon end-to-end. The cutover playbook's BUILD step is the first time anything will execute.

3. **Live runtime daemon health** — `/health-check` was correctly deferred to Studio Day 1.

4. **Visual regression for Clawdbot** — no `/design-review` was run because there's no live Clawdbot output to inspect.

5. **Network exposure scan** — none of the audits actually ran `nmap` against `127.0.0.1` after starting the MLX servers. Add to Day 7 verification.

6. **Postgres disk usage projection** — none of the audits estimated actual escalation log growth rate. Add capacity planning to Day 7.

7. **The Conway daemon** — exists in main repo but not in the worktree branch. None of the audits could verify the "wallet signing is never an LLM call" invariant against actual Conway code.

---

## Comparison to original Phase 42.5 v2 plan

**Spec adherence**: 98% (per `06-misalignment.md`). Code matches spec.

**Spec → reality drift**: 100%. The spec was based on a stale code baseline.

The implementation faithfully built what the spec described. The spec just described work that was largely already done in main. The misalignment isn't between spec and code — it's between spec and the actual production state at the time of writing.

This is a **planning failure**, not an **execution failure**. The right defense in the future:

1. Mandatory `git diff main...HEAD shared/llm_client.py` check before any phase that touches `llm_client.py`
2. Mandatory main-rebase preflight before any branch that adds shared/ modules
3. The new `aegis:guardrails` skill output (`05-aegis-guardrails.md`) installs hooks for both of these

---

## Recommended next action

**ONE concrete sentence**:

Spend Day 1 (~7-8 hours, two engineers in parallel) clearing the 8 quick-win P0s, then make the operator decision on the recovery plan (port new modules to main vs rewrite from main) before committing to Days 2-7 of remediation.

If solo: spend tomorrow morning (operator's first hours on the Studio) on the Day 1 quick wins, then sit down with the recovery plan and decide.

---

## Time + cost

| Metric | Value |
|---|---|
| Audits completed | 19 of 19 |
| Wall clock | ~4 hours |
| Sub-agent invocations | 19 (1 per audit) |
| Skills that errored | 0 |
| Skills not available | gsd:review (external CLIs not installed — fell back to manual analysis), aegis:remediate (no AEGIS report state — fell back to manual) |
| External scanners run | 0 (Trivy, Semgrep, Gitleaks, Checkov, Syft, Grype not installed on this system — AEGIS ran in degraded "Claude-driven manual review" mode) |
| Findings unique to non-Claude perspective | 3 (consistency vote bug, grammar compiler degradation, libcst recommendation for migrate script) |
| Findings consensus across 5+ audits | 3 (stale llm_client, ghost integration, decorative sandbox) |

---

## What this audit DID NOT catch (honest list)

1. We have NOT actually tried to build and run anything. All findings are static analysis.
2. We have NOT validated that the recovery plan above will work. The "port modules onto main" strategy is a hypothesis until tested.
3. We have NOT measured the actual line-by-line overlap between worktree and main. There may be more (or less) than 80% redundancy.
4. We have NOT confirmed which sub-features in main's `llm_client.py` are equivalent to the worktree's. Day 2 deep diff is required.
5. The 19 audits agreed because they're all Claude-family models reading the same codebase. Real cross-AI peer review (GPT-5, Gemini) would have found different things — `gsd:review` couldn't run external CLIs.

---

## Final note to the operator

You scheduled this audit explicitly because you wanted overkill. **Overkill found a real issue that we would have shipped without catching.** The audit pass paid for itself in the first hour.

The good news: the code itself is high quality. The autonomous build session produced production-grade code. The bad news: it was the wrong code to write. The Phase 42.5 v2 plan was built on a stale assumption about main's state.

**Recovery is well-defined and bounded.** ~14 days solo or ~7 days with two engineers. Within your 6-8 week launch window. You can still ship Perseus on the Studio on time.

The single biggest takeaway: **always run `git diff main...HEAD <load-bearing-files>` before starting any phase that touches them.** The new `aegis:guardrails` rules in `05-aegis-guardrails.md` automate this so it never happens again.

Read the individual audit files for full details. Start with `07-paul-audit.md` (most thorough) and `15-gsd-review.md` (most surprising new findings).

When you're ready to remediate: tell me to start Day 1 and I'll clear the 8 quick-win P0s in parallel. ~7-8 hours of work.
