# /igus — Master Audit Orchestrator
**Date**: 2026-04-07
**Audit target**: /Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/ (WHOLE codebase)
**Verdict**: SHIP-WITH-FIXES

## Findings

### Execution summary
- Preset: standard (adapted to focus areas: security, architecture integration, operational risk)
- Files in scope: whole worktree including 50 Phase 42.5 v2 files from commits 30770c0, 53dfecc, d9fdc6a
- AEGIS: not invoked in this wrapper pass (AEGIS runs in batch 0 step 2 per docs/audits/pre-launch/RUN.md); IGUS fell back to built-in analyzers on the focus-area slice
- Stages run: 0 (context), 1 (signal), 2 (security-focused domains), 4 (adversarial), 5 (synth), 6 (secrets), 7 (OWASP/CWE), 9 (language-agnostic), 10 (scoring)
- Stages skipped: 8 (progressive depth — time budget), 11 (auto-fix — flag off), 12 (memory — no .igus/ state yet)

### Score
- Raw score: 76/100
- Letter grade: C+
- Hard gate triggered: one HIGH severity finding below reaches >0.8 confidence but does not cross the `critical` hard-gate threshold. Grade is NOT promoted above B+ because of the architecture integration gap.

### Top findings (ranked by severity x confidence)

**H1 — HIGH — Phase 42.5 v2 LiteLLM integration does not exist in `shared/llm_client.py`** (CWE-1008 Architectural Principle Violated / A04:2021 Insecure Design)
- File: `shared/llm_client.py` (257 lines, NOT 880 as claimed in commit 30770c0 and .paul/HANDOFF.md)
- Evidence: `grep -n "LiteLLM|litellm|LITELLM|router|Router"` on `shared/llm_client.py` returns zero matches. The file still contains only the legacy Claude+Ollama budget-aware client with `_COST_PER_1K` dict keyed by `{haiku, sonnet, opus}`.
- Commit 30770c0 body itself acknowledges this: *"LiteLLMBackend class in shared/llm_client.py (needs careful integration with existing 880-line client — best done with operator review)"*. The 880-line number was wrong even when that commit was written.
- Impact: every Phase 42.5 v2 artifact (shared/tiers.py, shared/semantic_cache.py, shared/tier_classifier.py, shared/verifier/*, shared/aider/*, config/litellm_config.yaml, scripts/migrate_to_litellm.py, all 9 tests in tests/test_phase4{0,1,2,3,4}*.py) is dead code until a LiteLLMBackend is wired into LLMClient. None of the new tier registry, cache, verifier layers, or escalation log actually intercept real daemon traffic.
- This is not a security bug — it is a "SHIP-WITH-FIXES" architecture gap that invalidates the claim "Phase 42.5 is ready to cut over". Until the backend is written, Perseus is running on the legacy 257-line client.
- Confidence: 0.95
- Suggested fix: operator-reviewed implementation of `LiteLLMBackend` in `shared/llm_client.py` per plan 42-5-01 AC-5. Do NOT flip the cutover playbook until this exists and `tests/test_phase40_litellm_backend.py` passes against it.

**H2 — HIGH — Verifier sandbox profile exists but runner + red-team test are missing** (CWE-732 Incorrect Permission Assignment / A04:2021)
- File: `litellm/sandboxes/verifier.sb` references `scripts/run_in_sandbox.sh` and `tests/test_verifier_sandbox_red_team.py`. Neither exists on disk.
- Evidence: `ls scripts/run_in_sandbox.sh` → No such file. `ls tests/test_verifier_sandbox_red_team.py` → No such file.
- Impact: The `.sb` policy is well-written (deny default, explicit allowlists for /tmp scratch, blocks ~/.ssh, ~/.aws, wallets, Perseus repo, network) but NOTHING activates it. The Aider `ruflo_loop.py` and `clawdbot_loop.py` describe calling "pytest in sandbox" but have no code path that wraps execution with `sandbox-exec -f litellm/sandboxes/verifier.sb`. If cutover flips with loops enabled, prompt-injected fixes from the Editor model run with full daemon privileges on the Mac Studio — exact threat model the profile is supposed to block.
- Confidence: 0.90
- Suggested fix: write `scripts/run_in_sandbox.sh` (wrap `sandbox-exec -f litellm/sandboxes/verifier.sb -- "$@"`), call it from `RufloAiderLoop._run_verifier()` and `ClawdbotAiderLoop._run_verifier()`, and add the red-team canary test before any Aider loop runs live.

**H3 — HIGH — `CONWAY_KEYSTORE_PASSWORD` and `LITELLM_MASTER_KEY` are not documented in `.env.example`** (CWE-522 Insufficiently Protected Credentials / A02:2021)
- File: `.env.example`
- Evidence: `grep "CONWAY_KEYSTORE_PASSWORD|LITELLM_MASTER_KEY"` on `.env.example` returns zero matches. Both vars are load-bearing:
  - `shared/escalation_log/redactor.py:157` hashes `CONWAY_KEYSTORE_PASSWORD` for AES-GCM at-rest encryption; if unset the logger writes unencrypted and only logs a warning.
  - `config/litellm_config.yaml:253` requires `LITELLM_MASTER_KEY` for proxy auth; without it the proxy falls back to open-access admin.
- Impact: operator provisioning the Studio will miss these env vars because `.env.example` is the canonical checklist. Silent fallback to unencrypted escalation logs is worse than a hard crash because it is invisible until an audit opens the file. Two separate P0 safety mechanisms depend on vars the onboarding checklist does not mention.
- Confidence: 0.95
- Suggested fix: append both to `.env.example` with `CHANGE_ME_TO_A_LONG_RANDOM_SECRET` placeholders, AND change `EscalationLogger.__init__` to raise `RuntimeError` instead of `logger.warning` when the key is missing and the log path is not in /tmp.

### Medium findings

**M1 — MEDIUM — Redactor `base64-blob` pattern will destructively shred normal content**
- File: `shared/escalation_log/redactor.py:57`
- Pattern `\b[A-Za-z0-9+/]{40,}={0,2}\b` matches any 40+ char alphanumeric run. This will trigger on SHA-256 hashes that are NOT secrets, JWTs (but JWT already has its own rule above so this is dead-code there), base64-encoded image thumbnails in logs, git object hashes concatenated, pytest failure hashes, model response hashes. The `re.subn` calls are chained so the base64 rule runs LAST against the already-redacted text but it still catches `[REDACTED:openai-key]` substrings? No — that bracketed text contains `[` which breaks the regex word boundary, so it is safe from that. But the rule still over-matches.
- Impact: log content becomes unreadable when escalation events include long hashes. Not a security bug, but severely degrades the escalation log's debugging value, which is its stated secondary purpose.
- Confidence: 0.75
- Suggested fix: drop the generic base64 rule, or gate it on context keywords (`token=`, `secret=`, `authorization:`) only.

**M2 — MEDIUM — `DASHBOARD_HOST=0.0.0.0` default exposes Hermes war room on LAN**
- File: `.env.example:104`
- Default binding of the Hermes dashboard to `0.0.0.0:8500`. On a Mac Studio in an operator's home network this is LAN-accessible. Dashboard token default `CHANGE_ME_TO_A_RANDOM_TOKEN` is also clearly a placeholder and empty-string allows "open access (local dev only)" per the comment — but the default host is the opposite of local-dev-only.
- Confidence: 0.85
- Suggested fix: change default to `127.0.0.1`; add comment that `0.0.0.0` requires setting `DASHBOARD_SECRET` AND adding a macOS firewall rule.

**M3 — MEDIUM — `titan/training.py:451` uses `shell=True` with f-string composition for SSH**
- File: `titan/training.py:447-460`
- Composing `ssh ... root@{ssh_host} 'cd /workspace && nohup python3 train.py ...'` via `subprocess.run(..., shell=True)`. `ssh_host` appears config-controlled (Vast.ai), but a compromised config file becomes RCE on the training host via injection into the ssh_opts or ssh_host string.
- Confidence: 0.70
- Suggested fix: use argv form `subprocess.run(["ssh", *shlex.split(ssh_opts), f"root@{ssh_host}", "cd /workspace && ..."])` with shell=False.

**M4 — MEDIUM — Docker compose `host.docker.internal:11434` in `docker-compose.yaml:59` and `host.docker.internal:11437` in LiteLLM config**
- Docker is being kept for Hermes/LiteLLM per `docker-compose.yaml` but the native-services setup runbook flips to launchd. The two coexist post-cutover. Callers pointing at `host.docker.internal` will not resolve when services are native and clients are native too (they need `127.0.0.1`). This is a cutover bug, not a security bug.
- Suggested fix: runbook step should scan and swap `host.docker.internal` → `127.0.0.1` in all env files during native service cutover.

### Low / informational

**L1 — LOW — `shared/verifier/grammar_compiler.py:8` comment assumes `mlx_lm.server`** but the plan pivoted to Ollama MLX 4-bit. Doc comment is stale.

**L2 — LOW — 9 Phase 4x test files import modules (`shared.tiers`, `shared.semantic_cache`, `shared.verifier.*`, `shared.aider.*`) but there is no CI job asserting they import cleanly without the MLX stack.** Running `pytest tests/test_phase4*.py` on a machine without ollama/mlx available will fail at import time.

**L3 — LOW — Escalation log `log_path` default `/opt/perseus/data/escalation_log.jsonl.enc`** is created unconditionally via `mkdir(parents=True, exist_ok=True)` but `/opt/perseus/` is not documented to exist as a Perseus filesystem root anywhere in the runbooks. The native-services-setup runbook creates `/opt/perseus/runtime` for binaries but not `/opt/perseus/data`.

**L4 — INFO — Commit message claims 880-line llm_client.py; file is 257 lines.** Either the commit body was speculative or a refactor was dropped. Operator should reconcile.

**L5 — INFO — `shared/semantic_cache.py` default-deny allowlist is well-designed.** Explicit forbidden list includes `code_generation`, `email_compose`, `aider_architect`, `aider_editor` — exactly the right call. No finding, flagged as a bright spot for the summary orchestrator.

**L6 — INFO — Redactor canary test is a solid defense-in-depth primitive** but `canary_test()` is not wired into any startup check. Nothing calls it at daemon boot. One-line fix: call `redactor.canary_test()` in `EscalationLogger.__init__` and raise if `canaries_missed` is non-empty.

### Domain scorecard

| Domain | Score | Notes |
|---|---|---|
| secrets | 82 | Redactor pattern library is strong; missing .env.example docs; canary test not bootstrapped |
| auth | 78 | Dashboard default host, missing LITELLM_MASTER_KEY docs |
| injection | 85 | One shell=True in training.py; new Phase 42.5 code does not use shell=True |
| crypto | 80 | AES-GCM with SHA-256-derived key is OK; warning-not-error fallback is a gap |
| input-validation | 88 | Default-deny cache allowlist, well-scoped JSON schema → GBNF pipeline |
| infrastructure | 70 | Sandbox profile exists but runner missing; Docker/native service overlap |
| dependencies | n/a | Not scanned this pass |
| code-quality | 75 | Phase 42.5 modules are cleanly written; integration with legacy client is missing |
| financial | 85 | Per-daemon team budgets in LiteLLM config are well-scoped ($15-$90/month) |
| error-handling | 78 | Redactor `logger.warning` silent-unencrypted fallback is the main concern |

### OWASP/CWE mapping summary

- A02:2021 Cryptographic Failures — H3, M1 (CWE-522, CWE-311)
- A03:2021 Injection — M3 (CWE-78)
- A04:2021 Insecure Design — H1, H2 (CWE-1008, CWE-732)
- A05:2021 Security Misconfiguration — M2, M4 (CWE-16)
- A09:2021 Logging & Monitoring Failures — L6 (CWE-778)

### Hard gates evaluation

- Critical findings with confidence ≥ 0.8: 0
- Confirmed secret exposure: 0 (none detected in committed files)
- High findings: 3 — exceeds "more than 5 → grade cap B" gate? No, 3 ≤ 5. Grade cap does not trigger.
- SQL injection with confirmed reachability: 0
- Result: no hard gate triggered. Raw score 76/100 → C+. Architecture gap H1 is the dominant penalty.

### Delta vs baseline

No baseline available (first IGUS run on this worktree).

### Recommended next actions before launch

1. BLOCK launch if any auto Aider loop would run before H2 (sandbox runner + red-team test) is in place.
2. SHIP-WITH-FIXES on the following, in this order:
   a. Append `LITELLM_MASTER_KEY` and `CONWAY_KEYSTORE_PASSWORD` to `.env.example` (H3) — 5 minute fix
   b. Change `EscalationLogger` unencrypted fallback from warning to hard fail (H3) — 10 minute fix
   c. Write `scripts/run_in_sandbox.sh` + red-team canary test (H2) — 1-2 hours
   d. Implement `LiteLLMBackend` in `shared/llm_client.py` with operator review (H1) — 1-2 days; this is the real cutover gate
   e. Flip `DASHBOARD_HOST` default to `127.0.0.1` (M2)
   f. Convert `shell=True` SSH in training.py to argv form (M3)
3. Defer to batch 1: `/cso` will re-examine secrets and crypto, `/aegis:audit` will do full 14-domain pass, `/misalignment-detector` will scan for drift. This IGUS pass covers wave 1-7 on the focus areas; batch 1+ covers the rest.

### What IGUS did NOT cover

- Stage 11 auto-fix (flag off)
- Stage 12 memory (no `.igus/` directory initialized yet)
- Full 14-domain AEGIS scan (delegated to `/aegis:audit` in batch 0 step 2)
- deerflow/, openjarvis/, conway/, clawdbot/ daemon-specific audits (delegated to per-daemon skills in batches 2-3)
- Dependency CVE scan (delegated to `/cso`)
- Performance regression (delegated to `/benchmark` if operator schedules it)

### Bright spots (no finding, call out for orchestrator synthesis)

- `shared/escalation_log/redactor.py` pattern library is comprehensive (OpenAI, Anthropic, Google, AWS, GitHub, Stripe, JWT, PII, crypto wallets) — rare to see a first-pass redactor this thorough.
- `shared/semantic_cache.py` default-deny allowlist with explicit code-generation block is exactly the right shape.
- `litellm/sandboxes/verifier.sb` policy is well-written even though the runner is missing.
- `shared/tiers.py` 11-tier registry with explicit downgrade/upgrade chains and LOCAL_HEAVY deferral documented inline is clean.
- `config/litellm_config.yaml` per-daemon team budgets are scoped correctly ($15 conway ... $90 titan).
- Commit 30770c0 is honest about what was NOT done (LiteLLMBackend, sandbox red-team test, shadow mode runs) — the gap is documented in the commit message itself.

### Verdict rationale

SHIP-WITH-FIXES — not BLOCK because:
- No confirmed secret exposure
- No critical findings
- The architecture gap (H1) is known and documented in the commit body and .paul/HANDOFF.md
- The sandbox gap (H2) only matters if Aider loops are flipped live, which the cutover playbook already gates behind operator confirmation
- The .env gap (H3) is a 5-minute fix

Not SHIP because:
- The 3 HIGH findings need to clear before the cutover playbook runs
- Phase 42.5 v2 claims to be "ready to cut over" but the integration into the existing `LLMClient` is a TODO, not done

Recommended path: fix H3 + M2 + M3 now (under 1 hour), fix H2 before any Aider loop goes live (2 hours), land H1 with full review and tests before the cutover FLIP step.
