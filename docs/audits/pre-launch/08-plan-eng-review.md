# 08 — Plan Eng Review (Implementation)

**Target:** Phase 42.5 v2 implementation in worktree `charming-elion`
**Commits:** 30770c0, 53dfecc, d9fdc6a (50 files, 1 autonomous session)
**Reviewer:** plan-eng-review (eng manager mode)
**Date:** 2026-04-07

## VERDICT: BLOCK

This cannot ship as-is. The worktree is built on a stale llm_client.py that does not exist on main, and several components that look safe on paper silently degrade to unsafe behavior at runtime. The implementation is thoughtful but disconnected from the integration surface it needs to land on.

---

## Top execution risks

### R1 — Worktree stale-base integration break (CRITICAL, confidence 10/10)
`shared/llm_client.py` in this worktree is **257 lines** — the old Claude-primary + Ollama fallback client. Main branch has **1711 lines** with the full tier/operation/daemon_name surface. Every new module in this commit series imports `llm_client.generate(model=..., daemon_name=..., pipeline_stage=..., ...)` expecting the new signature.

- Worktree `LLMClient.generate()` signature: `(prompt, *, system, model, max_tokens, temperature, client_id, pipeline_stage)`
- New callers pass: `daemon_name`, `operation`, `tier`, plus vision/image kwargs

When this worktree is merged into main, either:
1. main's 1711-line client stays and the 257-line file is dropped (likely git conflict resolution), in which case all the new modules still need rework to match main's real signature (which the worktree author never saw), OR
2. The 257-line file overwrites main (catastrophic — kills LiteLLM proxy integration, kills team budgets, kills Langfuse callbacks).

**This worktree was written against a fiction.** The tier system code cannot be validated until it is rewritten against main's actual llm_client.py.

### R2 — Ruflo Aider loop silently bypasses the sandbox (CRITICAL, confidence 10/10)
`shared/aider/ruflo_loop.py:152` defaults `sandbox_runner=None`. At line 207-209:
```
if sandbox_runner is None:
    logger.warning("No sandbox_runner provided, skipping verification")
    last_verify = VerifierResult(passed=False, ...)
```
The loop treats "no sandbox" as "verifier failed" but still returns the patch as `last_patch` for escalation. Worse: no caller in the repo passes a `sandbox_runner` yet, and `litellm/sandboxes/verifier.sb` has **no Python wrapper** that invokes `sandbox-exec`. The sandbox is a config file with nothing calling it.

Net result: Ruflo Aider runs in the host process with full filesystem and network access. The "4-layer verifier" story in the commit message collapses at layer 3.

### R3 — Silent unencrypted escalation log (HIGH, confidence 9/10)
`shared/escalation_log/redactor.py:209` logs a warning when `CONWAY_KEYSTORE_PASSWORD` is missing and then **writes plaintext JSONL anyway**. The escalation log contains scrubbed-but-not-guaranteed-clean daemon payloads. If the env var isn't set (and there's no .env template entry or launchd setup doc for it), every daemon dumps operational traces in plaintext to disk with no rotation (see R4).

---

## Top integration breaks

### I1 — Missing env vars, no template updates (HIGH, confidence 10/10)
- `CONWAY_KEYSTORE_PASSWORD` — referenced in `shared/escalation_log/redactor.py:157`, no .env.example entry, no launchd `EnvironmentVariables` block, no doc telling the operator to set it.
- `LITELLM_MASTER_KEY` — referenced in `config/litellm_config.yaml:253`, only other mention is a monkeypatch.delenv in one test. No .env.example, no startup validator, no launchd wiring. Proxy will boot with `os.environ/LITELLM_MASTER_KEY` literal as the key.

### I2 — migrate_to_litellm.py silently skips multi-line calls (HIGH, confidence 10/10)
`scripts/migrate_to_litellm.py:249-250`:
```
if "llm.generate(" not in line and "llm_client.generate(" not in line:
    continue  # Multi-line call — skip for safety, hand-migrate
```
The check is done on **the call's reported first line only** — any `await llm.generate(\n    prompt,\n    ...)` pattern (the dominant style in this codebase) matches `generate(` on line 1 but the kwargs it needs to inject go on a line that's later patched by byte offset into the wrong position. The dry-run will report "N call sites migrated" but leave most of them half-patched or syntactically broken. The patcher needs to either (a) use `libcst` for concrete-syntax rewrites, or (b) explicitly detect multi-line by checking if the call's closing paren is on `site.line`.

### I3 — Semantic cache default-deny not enforced at caller level (MEDIUM, confidence 7/10)
The commit message claims semantic_cache forbids `code_generation`, `email_compose`, and all Aider calls. The Aider loops in `shared/aider/*.py` don't pass `operation=` to `llm_client.generate`, so the cache has no way to check the allowlist — it relies on the caller self-reporting. Any daemon that forgets the kwarg silently gets cached code generation.

---

## Top race conditions

### C1 — LeadWorkerLoop shared context dict (MEDIUM, confidence 7/10)
`shared/lead_worker.py:157` mutates `context[f"step_{step.id}_output"] = result.output` inside the loop. The loop itself is sequential (safe), but the `context` dict is passed by reference to `plan_fn`, `execute_fn`, and `review_fn` — all awaitables. If any callback spawns a background task (e.g., Titan's pipeline often does), that background task sees a context that keeps mutating under it. No copy, no lock, no generation number. Latent landmine for any daemon that fans out within a step.

### C2 — SelfConsistencyChecker n=3 sampling is not actually parallel (LOW severity / MEDIUM relevance, confidence 9/10)
`shared/verifier/consistency.py:115-128` samples the n-1 extra responses **sequentially** in a for-loop with `await`. There is no race because there is no parallelism — but this is the wrong fix for the stated goal. Three sequential Qwen3-30B calls at ~2-4s each adds 4-8s of blocking latency to every triggered call. If the trigger rate is anywhere near the claimed 15%, p95 latency on Titan/Hermes pipelines will balloon. Should be `asyncio.gather`. And once it is parallel, the shared `llm_client` HTTP client pool becomes the contention point — at which point the real race is on `_get_http()` lazy init (llm_client.py:36-39, `is_closed` check is not atomic under asyncio reentry).

---

## Memory pressure on 36GB Mac Studio

With Postgres + Qdrant + Mem0 + N8N + 8 daemons + hot MLX model set (Qwen3-30B-A3B + Coder-14B + 8B + VL-7B embeddings + Parakeet + Kokoro), the working set is ~24-28GB before OS + filesystem cache. The plan's AirLLM Llama 70B heavy tier **cannot coexist** with the hot set — AirLLM streams from disk but still needs ~8GB resident for active layers. First heavy-tier call during a full working day will thrash.

No code in this commit series enforces an MLX model eviction policy. No mlx-lm server restart, no LRU on loaded models, no memory pressure watchdog. The machine will OOM into swap within 24h of real use. This is a capacity planning gap, not a bug, but it blocks launch.

## Launchd plist edge cases

Reviewed `scripts/launchagents/com.perseus.master.plist`: `KeepAlive=true`, `ThrottleInterval=10`, `RunAtLoad=true`. Good baseline. Gaps:
- No `ExitTimeOut` — launchd will SIGKILL after 20s default. Ruflo mid-Aider iteration loses the in-flight patch and sandbox state. No checkpoint file.
- No `EnvironmentVariables` block for `CONWAY_KEYSTORE_PASSWORD` / `LITELLM_MASTER_KEY`. On Studio reboot, daemons start without credentials and (per R3) silently fall back to unsafe modes.
- `ThrottleInterval=10` on a crash-looping MLX server becomes a DDoS on Anthropic when every failed local call escalates to cloud.

## Escalation log rotation

Zero rotation. `grep -rn rotate` in `shared/escalation_log/` returns nothing. No `RotatingFileHandler`, no size cap, no `logrotate(8)` integration, no launchd periodic. Every escalation appends forever. On a day with 10k escalations at ~2KB each, that's 20MB/day. On a pathological day (MLX crash → cascade escalations), it can hit 1-10GB/day. 512GB SSD → full in weeks under normal operation.

## Sandbox subprocess wrapper missing

`litellm/sandboxes/verifier.sb` is a macOS sandbox-exec profile. **No Python code in the repo invokes `sandbox-exec`** with this profile. `grep -rn "sandbox-exec\|sandbox_exec"` returns zero hits in .py files. The verifier L3 story requires a wrapper like:
```python
subprocess.run(["sandbox-exec", "-f", "litellm/sandboxes/verifier.sb", "pytest", ...])
```
This wrapper does not exist. The .sb file is decorative until someone writes it.

---

## Critical gaps summary

| # | Gap | Severity | Confidence |
|---|-----|----------|------------|
| 1 | Worktree based on stale 257-line llm_client (main is 1711 lines) | CRITICAL | 10/10 |
| 2 | Ruflo sandbox_runner=None silently bypasses verification | CRITICAL | 10/10 |
| 3 | No sandbox-exec Python wrapper — verifier.sb is decorative | CRITICAL | 10/10 |
| 4 | CONWAY_KEYSTORE_PASSWORD missing → plaintext escalation log | HIGH | 9/10 |
| 5 | LITELLM_MASTER_KEY missing from .env template + launchd | HIGH | 10/10 |
| 6 | migrate_to_litellm.py mis-patches multi-line calls | HIGH | 10/10 |
| 7 | Escalation log has no rotation | HIGH | 10/10 |
| 8 | 36GB Mac Studio OOM under hot model set + AirLLM | HIGH | 8/10 |
| 9 | Launchd no ExitTimeOut, no env vars block | MEDIUM | 9/10 |
| 10 | Semantic cache allowlist enforced at caller, not at cache | MEDIUM | 7/10 |
| 11 | LeadWorker shared context dict mutable during callbacks | MEDIUM | 7/10 |
| 12 | Consistency checker is sequential — adds 4-8s p95 | LOW | 9/10 |

## Required before ship

1. **Rebase worktree onto main's real llm_client.py.** Rewrite every new module's LLM call to match the actual tier signature on main. Nothing else matters until this is done.
2. **Write the sandbox wrapper.** `shared/sandbox/exec.py` with `run_in_sandbox(cmd, profile=".../verifier.sb")`. Wire it into ruflo_loop and clawdbot_loop. Make `sandbox_runner=None` a hard failure, not a warning.
3. **Add .env.example entries** and `scripts/preflight.py` that refuses to start daemons when any required env var is missing.
4. **Replace migrate_to_litellm.py with libcst** or fix the multi-line detection. Add a syntax-check gate in dry-run.
5. **Add RotatingFileHandler** to escalation_log (50MB × 5 files) and a nightly launchd job that ships older files to Conway's encrypted cold storage.
6. **Memory governor**: add `shared/mlx_governor.py` that evicts loaded models under memory pressure. Refuse AirLLM heavy tier when RSS > 28GB.
7. **Fix launchd plists**: add `ExitTimeOut=120`, `EnvironmentVariables` block, per-daemon throttle tuned to failure mode.

## NOT in scope for this review

- The LangFuse eval config (reviewed elsewhere)
- The Redis HNSW semantic cache implementation details (needs its own review)
- Migration rollback correctness (rollback_litellm.py unread)
- Phase 44 golden prompts coverage quality

## Review log entry

```
{"skill":"plan-eng-review","timestamp":"2026-04-07T00:00:00Z","status":"issues_open","unresolved":0,"critical_gaps":3,"issues_found":12,"mode":"FULL_REVIEW","commit":"d9fdc6a"}
```
