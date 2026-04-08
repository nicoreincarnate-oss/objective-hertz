# AEGIS Remediation Plans — Pre-Launch Audit

**Source findings:** `docs/audits/pre-launch/02-aegis/` (7 P0, 10 P1, 17 P2, 4 P3)
**Generated:** 2026-04-07
**Mode:** Manual remediation (Layer A audit output lives under `docs/audits/pre-launch/02-aegis/`, not `.aegis/findings/`; Transform Phase 6 agent workflow could not auto-consume it)
**Intervention level:** Planning (human approval required before apply)
**Scope:** 7 P0 + 10 P1 = 17 remediation plans
**Total estimated effort:** ~61 hours

---

## ROI Summary & Priority Order

| Rank | ID | Severity | Effort | ROI | Title |
|------|----|---------|--------|-----|-------|
| 1 | P0-6 | P0 | 0.5h | CRITICAL | CONWAY_KEYSTORE_PASSWORD missing env var |
| 2 | P0-1 | P0 | 1.5h | CRITICAL | Stale `shared/llm_client.py` rebase gap |
| 3 | P0-4 | P0 | 1.0h | HIGH | Sandbox tilde paths unexpanded on macOS |
| 4 | P0-5 | P0 | 1.5h | HIGH | Unsalted SHA-256 in escalation log |
| 5 | P0-7 | P0 | 2.0h | HIGH | Multi-line `llm.generate()` breaks migrator |
| 6 | P0-3 | P0 | 3.0h | HIGH | Verifier sandbox profile unreachable |
| 7 | P0-2 | P0 | 8.0h | HIGH | Phase 42.5 v2 ghost integration |
| 8 | P1-1..10 | P1 | ~44h | MED | See P1 section |

**Quick wins (<2h, high ROI):** P0-6, P0-1, P0-4, P0-5.

---

# P0 FINDINGS

## P0-1 — Stale `shared/llm_client.py` rebase gap (1,454 lines missing)

**Evidence:** Worktree `charming-elion` has 257 lines; `main` has 1,711 lines. Worktree branched before Phase 42.5 v2 landed on main.

**Root cause:** Long-lived worktree missed rebase from main after 30770c0 (Phase 42.5 write-all-artifacts).

**Files to modify:**
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/llm_client.py`
- any divergent daemon modules that import from it

**Fix steps:**
1. `git fetch origin main`
2. `git merge origin/main -- shared/llm_client.py` (path-restricted merge) OR full `git rebase origin/main` if worktree is otherwise stale.
3. Resolve conflicts preferring main's implementation (worktree version is ancestor).
4. Re-run `python -m py_compile shared/llm_client.py`.
5. Diff against main: `diff <(wc -l shared/llm_client.py) <(git show main:shared/llm_client.py | wc -l)` — expect equal line counts.

**Validation:** `pytest tests/shared/test_llm_client.py` + smoke-test one daemon import.

**Effort:** 1.5h
**Intervention level:** Authorizing (evidence is quantitative diff; low ambiguity)

---

## P0-2 — Phase 42.5 v2 modules not wired into any daemon (ghost integration)

**Evidence:** New v2 modules (local tier router, Aider architect-editor, voice loop, AirLLM tier) exist under `shared/local_tier/` but `grep -r "from shared.local_tier" perseus/ titan/ hermes/ clawdbot/ ruflo/` returns zero hits.

**Root cause:** Phase 42.5 autonomous-build wrote the artifacts (commit 30770c0) without wiring imports into daemon entrypoints. Contractor pattern: modules exist, call sites don't.

**Files to modify:**
- `perseus/main.py` — register local tier as LLM backend option
- `titan/pipeline/stages/*.py` — route non-critical stages through local tier
- `hermes/jarvis/llm_adapter.py` — add local tier fallback
- `clawdbot/agents/builder.py` — wire Aider architect-editor
- `ruflo/fix_swarm.py` — wire Aider for code fixes
- `openjarvis/engine/workflow_engine.py` — register local tier workers
- `shared/llm_client.py` — add `local_tier` provider enum (may already exist post-rebase)

**Fix steps:**
1. Create integration checklist by grepping each v2 module for its `# INTEGRATION POINT:` comments (if any).
2. For each daemon, add import + routing decision at the LLM-call boundary.
3. Gate behind `LOCAL_TIER_ENABLED` env var (default false) for safe rollout.
4. Add integration test per daemon verifying the import path resolves and a dummy call dispatches correctly.
5. Update `.env.example` with new env vars.

**Validation:**
- `python -c "import perseus.main; import titan.pipeline; import hermes.jarvis.llm_adapter; import clawdbot.agents.builder; import ruflo.fix_swarm; import openjarvis.engine.workflow_engine"` all succeed.
- Run `perseus/scripts/verify_local_tier_wiring.py` (to be created) which asserts each daemon has at least one `local_tier` call site.
- CI gate: new lint rule that fails if `shared/local_tier/*.py` has zero importers.

**Effort:** 8h
**Intervention level:** Planning (blast radius is large; requires human review of each daemon wiring)

---

## P0-3 — Verifier sandbox profile exists but is unreachable

**Evidence:** `sandbox/profiles/verifier.sb` exists; no Python wrapper invokes `sandbox-exec -f verifier.sb`; no red-team test confirms isolation.

**Root cause:** Profile authored ahead of the runner; wiring + test never landed.

**Files to modify:**
- `shared/sandbox/runner.py` — NEW: wraps `sandbox-exec -f <profile> -- <cmd>` with timeout, stdout/stderr capture, resource limits.
- `shared/sandbox/__init__.py` — export `run_sandboxed()`.
- `tests/security/test_sandbox_escape.py` — NEW: red-team suite attempting file reads outside `~/perseus-sandbox`, network calls to 1.1.1.1, fork bombs, `/etc/passwd` reads.
- Callers in Ruflo/Clawdbot that execute generated code.

**Fix steps:**
1. Write `run_sandboxed(cmd: list[str], profile: str, timeout: int) -> SandboxResult` using `subprocess.run(["sandbox-exec", "-f", profile_path, *cmd], ...)`.
2. Resolve profile path absolutely after tilde expansion (see P0-4).
3. Add red-team pytest cases — each MUST fail to escape (assert non-zero exit or empty output).
4. Wire Ruflo's code-exec path through `run_sandboxed()`.
5. Add CI job that runs the red-team suite on macOS.

**Validation:** `pytest tests/security/test_sandbox_escape.py -v` — all 8 escape attempts must be blocked.

**Effort:** 3h
**Intervention level:** Authorizing (security-critical; human approval before merge)

---

## P0-4 — Sandbox tilde paths don't expand on macOS `sandbox-exec`

**Evidence:** `verifier.sb` uses literal `~/perseus-sandbox` in `(allow file-read* (subpath "~/perseus-sandbox"))`. `sandbox-exec` does not expand `~`.

**Root cause:** Profile was written by LLM without macOS sandbox-exec syntax verification.

**Files to modify:**
- `sandbox/profiles/verifier.sb` — replace all `~` with `(param "HOME")` parameterization or absolute-path template substitution.
- `shared/sandbox/runner.py` — pass `-D HOME=$HOME` to `sandbox-exec` so profile can use `(param "HOME")`.

**Fix steps:**
1. Edit profile: change `(subpath "~/perseus-sandbox")` → `(subpath (string-append (param "HOME") "/perseus-sandbox"))`.
2. In runner, invoke as: `sandbox-exec -D HOME="$HOME" -f verifier.sb -- <cmd>`.
3. Add unit test that writes a file to `$HOME/perseus-sandbox/test.txt` from inside the sandbox and verifies it's allowed.
4. Add negative test: write to `$HOME/Desktop/test.txt` — must be denied.

**Validation:** `bash tests/security/sandbox_path_expansion_test.sh` passes.

**Effort:** 1h
**Intervention level:** Authorizing

---

## P0-5 — Unsalted SHA-256 key derivation in escalation log

**Evidence:** `conway/escalation_log.py` (or similar) uses `hashlib.sha256(key_bytes).hexdigest()` with no salt; rainbow-table exposure on low-entropy inputs.

**Root cause:** Developer used SHA-256 as KDF; should be scrypt/argon2/PBKDF2.

**Files to modify:**
- `conway/escalation_log.py` (exact path: grep for `hashlib.sha256` in conway/)
- `conway/crypto_utils.py` — NEW or existing: centralize KDF.
- `conway/tests/test_crypto.py`

**Fix steps:**
1. Replace SHA-256 derivation with `hashlib.scrypt(password, salt=os.urandom(16), n=2**14, r=8, p=1, dklen=32)` OR `argon2-cffi` with id variant.
2. Store salt alongside derived key (per-record salt).
3. Add versioning byte prefix so old records can be migrated.
4. Write migration script `conway/scripts/migrate_escalation_log_kdf.py` — reads old unsalted records, re-encrypts with new scheme, backs up original.
5. Add Known-Answer Test with fixed salt.

**Validation:** `pytest conway/tests/test_crypto.py::test_kdf_is_salted` and `test_migration_preserves_data`.

**Effort:** 1.5h
**Intervention level:** Authorizing (crypto change — human + CSO review)

---

## P0-6 — `CONWAY_KEYSTORE_PASSWORD` missing, silent plaintext fallback

**Evidence:** `conway/keystore.py` checks `os.getenv("CONWAY_KEYSTORE_PASSWORD")`; if unset, falls back to storing keys plaintext with a warning log.

**Root cause:** Defensive fallback was added during development and never removed. `.env.example` does not list the variable.

**Files to modify:**
- `conway/keystore.py`
- `.env.example`
- `docs/deployment/env-vars.md`
- `perseus/main.py` (startup preflight)

**Fix steps:**
1. In `conway/keystore.py`, raise `RuntimeError("CONWAY_KEYSTORE_PASSWORD must be set")` instead of plaintext fallback.
2. Add startup preflight in `perseus/main.py` that refuses to boot if any secure env var is missing.
3. Add `CONWAY_KEYSTORE_PASSWORD=change-me-before-prod` to `.env.example` with clear docs.
4. Update deployment runbook.

**Validation:** `unset CONWAY_KEYSTORE_PASSWORD && python -m perseus.main` → exits non-zero with clear error.

**Effort:** 0.5h
**Intervention level:** Authorizing (fail-closed security fix — highest ROI quick win)

---

## P0-7 — Multi-line `llm.generate()` calls break `migrate_to_litellm.py`

**Evidence:** The LiteLLM migrator uses a single-line regex; multi-line invocations (`llm.generate(\n  prompt=...,\n  model=...\n)`) are silently skipped.

**Root cause:** Regex-based AST rewriting is brittle for multi-line source.

**Files to modify:**
- `scripts/migrate_to_litellm.py`
- `tests/migration/test_litellm_migrator.py`

**Fix steps:**
1. Replace regex with `libcst` or `ast` module-based transform.
2. Build `LLMGenerateTransformer(cst.CSTTransformer)` that matches `Call(func=Attribute(attr=Name('generate')))` on any `llm.*` receiver.
3. Regenerate all call sites to use LiteLLM signature.
4. Add golden-file tests: input file → expected output file for single-line, multi-line, kwargs-only, positional, and chained-call variants.
5. Dry-run across repo: `python scripts/migrate_to_litellm.py --dry-run` — confirm ALL call sites identified.

**Validation:** `pytest tests/migration/test_litellm_migrator.py` and `grep -rn "llm.generate(" --include="*.py" | wc -l` before/after confirms migrator-processed files changed.

**Effort:** 2h
**Intervention level:** Planning (the migrator hasn't run yet; bug is in tooling not prod code)

---

# P1 FINDINGS

These are summarized to keep the document scannable. Each carries Planning-level intervention.

## P1-1 — LiteLLM provider routing lacks fallback chain
Files: `shared/llm_client.py`, `shared/litellm_config.yaml`
Fix: Add `fallbacks: [anthropic, openai, local_tier]` per model class. Tests: provider-kill chaos test.
Effort: 3h

## P1-2 — Aider architect+editor not integrated into Ruflo fix swarm
Files: `ruflo/fix_swarm.py`, `ruflo/agents/architect.py` (new)
Fix: Wire Aider two-phase flow; add benchmark vs single-shot.
Effort: 6h

## P1-3 — Parakeet STT + Kokoro TTS voice loop exists but no Hermes wiring
Files: `hermes/voice/loop.py`, `hermes/jarvis/session.py`
Fix: Expose `/voice/session` endpoint; add push-to-talk client.
Effort: 5h

## P1-4 — AirLLM Llama 70B heavy tier: no memory-pressure gate
Files: `shared/local_tier/airllm_runner.py`
Fix: Check free RAM before dispatch; queue if <12GB free; add metric.
Effort: 2h

## P1-5 — Kimi K2.5 cloud vision fallback has no cost cap
Files: `shared/vision/kimi_client.py`, `shared/budget_guard.py`
Fix: Route through Conway budget guard; hard daily cap.
Effort: 2h

## P1-6 — Embeddings model (Qwen3 8B) not pinned; auto-pull can swap versions
Files: `shared/local_tier/embeddings.py`, `Makefile`
Fix: Pin SHA in config; verify on startup; fail if mismatch.
Effort: 1h

## P1-7 — Titan revenue pipeline stage 6 has no idempotency key
Files: `titan/pipeline/stages/06_*.py`
Fix: Add idempotency_key column + upsert; retry-safe.
Effort: 4h

## P1-8 — Hermes Telegram bot lacks rate limit on command handlers
Files: `hermes/telegram/bot.py`
Fix: Token-bucket per chat_id; reject over-limit with friendly msg.
Effort: 2h

## P1-9 — Clawdbot browser automation holds Chrome processes on crash
Files: `clawdbot/browser/session.py`
Fix: Context manager with SIGTERM on __exit__; orphan reaper cron.
Effort: 3h

## P1-10 — DeerFlow research brief cache has no TTL (unbounded growth)
Files: `deerflow/cache/brief_cache.py`
Fix: Add TTL=14d; nightly eviction pass; size metric.
Effort: 2h

---

# Execution Sequence

**Wave 1 (day 1, ~5h):** P0-6, P0-1, P0-4, P0-5, P1-6 — all quick wins, security-critical, no coupling.
**Wave 2 (day 2, ~5h):** P0-3, P0-7, P1-4, P1-8 — tooling + single-daemon fixes.
**Wave 3 (days 3-5, ~12h):** P0-2 ghost integration (biggest risk; full daemon wiring).
**Wave 4 (week 2, ~20h):** P1-2, P1-3, P1-7, P1-9 — feature integrations.
**Wave 5 (week 2, ~9h):** P1-1, P1-5, P1-10 — polish + guardrails.

**Total:** ~61h = 1.5 engineer-weeks sequential, ~1 week parallel (3 streams).

---

# Safety Notes

- All plans are PROPOSED — no auto-apply. Human approval required per intervention level rules.
- P0-2, P0-3, P0-5, P0-6 are security/correctness gates — MUST complete before launch.
- P0-1 (rebase) must precede P0-2 (ghost integration) to avoid re-resolving conflicts.
- Sandbox changes (P0-3, P0-4) must be validated on macOS specifically — cannot be Linux-tested.
- No changes to `.aegis/STATE.md` — this remediation runs outside the Transform pipeline.
