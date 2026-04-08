# Perseus Pre-Launch Risk Assessment

**Date:** 2026-04-07
**Scope:** Forward-looking operational risks for launching Perseus on Mac Studio M4 Max (36GB) with Phase 42.5 v2 stack.
**Target Topology:** 8 daemons (perseus, titan, hermes, clawdbot, conway, deerflow_research, ruflo, openjarvis) + native macOS services (Postgres, Qdrant, Mem0, N8N, Redis) + MLX hot set (Qwen3-30B-A3B, Coder-14B, embeddings, reranker, Parakeet, Kokoro).

## Risk Matrix

| | Low Impact | Medium Impact | High Impact |
|---|---|---|---|
| **High Likelihood** | Medium | High | **Critical** |
| **Medium Likelihood** | Low | Medium | High |
| **Low Likelihood** | Low | Low | Medium |

## Risk Register (ranked by severity)

---

### R1 — Phantom Cutover: Flip happens, nothing changes, operator assumes success
**Category:** Operational / Strategic
**Probability:** HIGH (~80%) — the audit has confirmed most modules are not yet wired to daemons; litellm routing, MLX stack, and voice loop are largely unintegrated.
**Impact:** HIGH — silent failure of the entire Phase 42.5 v2 cutover. Operator believes Perseus is on local tier; reality is daemons still hit Anthropic or fall back to stub paths. Cost overrun, quality regression, and worst case: training data contamination from unexpected routing.
**Risk Level:** CRITICAL
**Blast radius:** All 8 daemons, all LLM traffic, all agent economics (Conway records bogus costs), training signal corruption.
**Mitigation:**
- NO all-at-once flip. Replace cutover playbook with per-daemon canary: one daemon → validate → next.
- Pre-flip assertion: each daemon must emit `phase_42_5_active=true` telemetry with model-name provenance on first request.
- Dry-run gate: block the flip until a `make verify-phase-42-5` script confirms every daemon routes through litellm and returns the expected local-tier model in response metadata.
- Post-flip smoke: 50-request probe per daemon, assert zero Anthropic API calls in `hermes.network_log`.
**Monitoring:** Hermes dashboard panel "Active Model by Daemon" (must show local model). Alert if any daemon emits Anthropic API call post-cutover.
**Owner:** Operator
**Status:** OPEN — blocker for launch.

---

### R2 — shared/llm_client.py stale by 1454 lines — naive merge silently reverts production
**Category:** Operational / Strategic
**Probability:** HIGH (~90%) — confirmed by audit; any standard `git merge main` on this worktree will produce a merge that discards 1454 lines of production evolution if conflicts are auto-resolved the wrong way.
**Impact:** HIGH — silent regression of LLM routing, retry logic, provider fallbacks, cost tracking. Corrupts every daemon.
**Risk Level:** CRITICAL
**Blast radius:** All LLM traffic. Every daemon. Every pipeline stage that touches LLM (Titan scoring, Deerflow research, Ruflo fixing, Clawdbot generation).
**Mitigation:**
- HARD BLOCK on `git merge main` into this worktree. Use `git merge main --no-commit` then manually inspect every hunk in `shared/llm_client.py`.
- Run `git diff main HEAD -- shared/llm_client.py | wc -l` as a pre-merge gate — if >100, escalate to 3-way diff review.
- Create a baseline tag `pre-phase-42-5-baseline` on main BEFORE any merge, so rollback is atomic.
- Add a CI check: `git diff main..HEAD -- shared/llm_client.py` must be reviewed human-in-loop before merge.
**Monitoring:** Post-merge, run `pytest tests/shared/test_llm_client.py` + snapshot the routing table. Diff pre/post routing tables before pushing.
**Owner:** Operator + code review
**Status:** OPEN — blocker for merge.

---

### R3 — Mac Studio reboot mid-cutover — dirty state across 13 processes
**Category:** Operational
**Probability:** MEDIUM (~30% over a 6-8 week cutover window) — macOS auto-updates, power events, MLX model OOM crashes can trigger.
**Impact:** HIGH — in-flight LLM requests lost; Postgres WAL may be mid-commit on pipeline state; Qdrant/Mem0 writes may be half-durable; Conway wallet state desync if USDC tx was in-flight; MLX models must reload (2-4 min cold start each).
**Risk Level:** HIGH
**Blast radius:** All daemons. Financial state if Conway was mid-transaction. Training data loss window.
**Mitigation:**
- Enable `defaults write com.apple.loginwindow TALLogoutCancel -bool true` and disable auto-update during cutover.
- Every daemon MUST use Postgres transactional checkpoint pattern (no dangling in-memory state > 30s).
- Launchd plists for every daemon with `KeepAlive=true` and `ThrottleInterval=30`.
- Conway wallet ops must be two-phase: (1) write intent row in Postgres, (2) sign+broadcast, (3) confirm — crash-safe at any point.
- MLX model warm-up script run on boot, fires health check after all models loaded.
- Runbook: `recover-from-reboot.md` with explicit step-by-step.
**Monitoring:** systemd-style boot health check that asserts all 13 processes up within 5min. Hermes alert if boot sequence exceeds 10min.
**Owner:** Operator
**Status:** OPEN — needs runbook.

---

### R4 — Ollama daemon crash takes down 70% of LLM traffic
**Category:** Operational
**Probability:** MEDIUM (~40% over first 30 days) — Ollama has known memory fragmentation issues under sustained load.
**Impact:** HIGH — 70% of traffic gone, no graceful degradation. Cascade: Titan stalls, Deerflow pauses, Clawdbot fails.
**Risk Level:** HIGH
**Blast radius:** 5 of 8 daemons lose primary LLM path.
**Mitigation:**
- litellm fallback chain MUST include non-Ollama path: MLX direct → Ollama → AirLLM → Kimi cloud.
- Circuit breaker on Ollama: 3 consecutive 5xx → route away for 60s.
- launchd KeepAlive + ThrottleInterval=10 for Ollama process.
- Pre-warm a secondary MLX server on different port as hot-standby.
**Monitoring:** Ollama health endpoint polled every 15s by Hermes. P50/P99 latency tracked. Auto-alert on crash.
**Owner:** Operator
**Status:** OPEN.

---

### R5 — Memory pressure: 36GB colocated OOMs under load
**Category:** Operational
**Probability:** MEDIUM (~50%) — hot set estimate: Qwen3-30B-A3B (~18GB active), Coder-14B (~9GB), embeddings+reranker (~2GB), Parakeet+Kokoro (~2GB), Postgres+Qdrant+Mem0+Redis+N8N (~4GB), 8 daemons (~2-3GB). That's already ~37GB before OS, caches, and burst.
**Impact:** HIGH — macOS starts swapping to SSD, latency explodes 10-100x. Eventually kernel OOM killer takes random process.
**Risk Level:** HIGH
**Blast radius:** Entire system throttles or random daemon dies.
**Mitigation:**
- Hot set reduction: run Qwen3-30B-A3B and Coder-14B mutually exclusive via lazy-load (only one in memory at a time). Route chooses.
- Pin swappiness / memory pressure thresholds via `sysctl vm.compressor_mode`.
- Move Mem0 and N8N to opportunistic load (start on demand, unload after 5min idle).
- Hard cap Postgres shared_buffers to 1GB, Qdrant to 1GB.
- Disable Spotlight indexing on model weight directories.
- Add `pressure-stall-watchdog` that drops the less-used MLX model when `memory_pressure > 80%`.
**Monitoring:** `vm_stat` + `memory_pressure` polled every 30s. Hermes alert on >70% sustained. Kill-switch at 90%.
**Owner:** Operator
**Status:** OPEN — needs load test before launch.

---

### R6 — Ruflo Aider with no sandbox produces malicious patch from poisoned lead
**Category:** Security
**Probability:** LOW-MEDIUM (~15% over 90 days) — prompt injection via scraped lead content or web search result is a known vector.
**Impact:** HIGH — Aider runs with repo write access. A crafted "fix this bug" lead could inject `rm -rf`, exfiltrate secrets from `.env`, or plant backdoor in `shared/llm_client.py`.
**Risk Level:** HIGH
**Blast radius:** Entire codebase. Secrets. Git history. Potentially USDC wallet if Conway keys are in env.
**Mitigation:**
- Run Ruflo Aider in a macOS `sandbox-exec` profile with write access ONLY to `workspace/ruflo-scratch/`. No access to `.env`, `~/.ssh`, Conway wallet dir, or repo root.
- All Aider-generated patches MUST go through a human-approved PR gate for the first 30 days.
- Strip secrets from environment before exec; pass only allowlisted vars.
- Content-sanitize all lead text before it reaches Aider: strip markdown code fences that match `rm|curl|wget|eval|exec|ssh-keygen`.
- Aider commits must be signed by a dedicated ephemeral GPG key, distinguishable from operator commits.
- Post-commit hook scans Ruflo commits for secret patterns and known injection signatures.
**Monitoring:** Every Ruflo commit auto-scanned. Any commit touching `.env`, `shared/llm_client.py`, or `conway/wallet/` triggers immediate Telegram alert.
**Owner:** Operator + Ruflo
**Status:** OPEN — sandbox profile not yet written.

---

### R7 — Conway USDC wallet key compromised
**Category:** Security / Financial
**Probability:** LOW (~5%) — but non-zero given R6 and colocated process model.
**Impact:** CRITICAL — all USDC drained from operator wallet on Base L2. Funds unrecoverable. Reputational damage.
**Risk Level:** HIGH
**Blast radius:** Full wallet balance. Any contract the wallet has approval on (could be more than balance).
**Mitigation:**
- Wallet key MUST NOT live in `.env` or process memory of any daemon other than Conway.
- Use macOS Keychain for Conway key storage, gated by biometric on sensitive ops.
- Two-wallet model: HOT wallet with max $50 balance for autonomous ops, COLD wallet for treasury (hardware-backed, operator-only).
- Daily sweep: balance above $50 auto-moves to cold wallet.
- Spend limits per tx ($5), per hour ($20), per day ($100) — enforced client-side in Conway AND via a separate guardian contract on Base.
- Revoke ALL outstanding ERC20 approvals; use per-tx approval pattern.
- Monitor cold wallet independently via Etherscan webhook.
**Monitoring:** Any tx >$10 pings Telegram. Any tx outside business hours pings Telegram twice. Daily balance reconciliation.
**Owner:** Operator
**Status:** OPEN — currently single hot wallet, no spend limits, no cold sweep.

---

### R8 — Postgres disk fills because escalation log isn't rotated
**Category:** Operational
**Probability:** MEDIUM (~60% over 90 days) — 512GB SSD, escalation log estimated at 50-200MB/day unbounded, plus Postgres WAL, plus MLX model caches, plus training data.
**Impact:** MEDIUM-HIGH — Postgres refuses writes when disk full. Every daemon that uses it stalls. Recovery requires manual VACUUM/log truncate with disk at 100%.
**Risk Level:** HIGH
**Blast radius:** All pipeline state. All memory graph (Neo4j on same disk). All training data.
**Mitigation:**
- Add `escalation_log` rotation: partitioned by month, auto-drop partitions >90 days old.
- pg_cron job: nightly VACUUM + delete soft-deleted rows older than 30 days.
- WAL archiving to external disk or S3-compatible storage.
- Disk space monitor: alert at 70%, hard-stop writes at 90%.
- Quota the MLX model cache to 100GB.
- Logrotate all daemon stdout/stderr to max 7 days / 500MB each.
**Monitoring:** `df /` polled every 5min by Perseus daemon. Alert at 70%. Auto-emergency cleanup at 85%.
**Owner:** Operator
**Status:** OPEN — no rotation policy exists yet.

---

### R9 — Operator unavailable and daemon needs human approval
**Category:** Operational
**Probability:** HIGH (~95%) — operator is one person, will be AFK regularly.
**Impact:** MEDIUM — autonomous ops stall. Revenue pipeline pauses. Training data accumulates stale.
**Risk Level:** HIGH
**Blast radius:** Autonomous decision velocity drops to zero during operator absence.
**Mitigation:**
- Tiered approval model: (a) auto-approve under well-defined bounds, (b) Telegram quick-approve (button click from phone), (c) escalate with 4hr deadline, (d) conservative default if no response.
- Every human-in-loop decision MUST have a declared conservative default ("if no response in 4hr, skip this lead").
- Daily digest to operator at 09:00 local of pending approvals.
- Deadman's switch: if operator hasn't touched the system in 48hr, Perseus auto-enters "read-only mode" — no new expensive ops, no USDC spend, no repo commits. Keeps learning but stops acting.
**Monitoring:** Hermes pending-approval queue. Telegram summary every 6hr of blocked tasks.
**Owner:** Operator
**Status:** OPEN — conservative-default policy not defined per decision type.

---

### R10 — Network outage, Hermes can't reach Telegram for alerts
**Category:** Operational
**Probability:** MEDIUM (~40% over 90 days) — home ISP outages, Telegram regional issues, DNS.
**Impact:** MEDIUM — operator is blind to critical events during outage window. Compounds other risks (can't learn about OOM, disk full, USDC drain).
**Risk Level:** MEDIUM-HIGH
**Blast radius:** Observability only, but observability is the meta-defense for everything else.
**Mitigation:**
- Multi-channel alert fan-out: Telegram + email (SMTP via outbound-only relay) + local macOS notification + audible Mac alert.
- Hermes buffers alerts locally in SQLite when Telegram unreachable; flushes on recovery. Buffer survives restart.
- Health heartbeat INVERSION: external cron-job.org / Healthchecks.io pings Hermes every 5min; if Hermes misses, the external service alerts operator via SMS. Detects local network outage from outside.
- Redundant uplink: 4G/5G hotspot as secondary ISP.
**Monitoring:** External Healthchecks.io status page. Hermes alert-queue depth panel.
**Owner:** Operator
**Status:** OPEN — no external heartbeat yet.

---

## Summary

**Total risks identified:** 10
**By severity:**
- CRITICAL: 2 (R1 phantom cutover, R2 stale llm_client.py merge)
- HIGH: 7 (R3 reboot, R4 Ollama crash, R5 OOM, R6 Ruflo sandbox, R7 wallet, R8 disk, R9 operator AFK)
- MEDIUM-HIGH: 1 (R10 Telegram outage)

**Top 5 by severity:**
1. R1 — Phantom cutover (CRITICAL, blocker)
2. R2 — Stale llm_client.py merge (CRITICAL, blocker)
3. R7 — USDC wallet compromise (HIGH, financial)
4. R5 — 36GB OOM under load (HIGH, capacity)
5. R6 — Ruflo no-sandbox (HIGH, security)

**Top 3 mitigations (highest ROI):**
1. **Per-daemon canary cutover + model-provenance telemetry** — kills R1 and partially mitigates R2, R4, R5 by forcing observability of the cutover.
2. **Hot/cold wallet split + spend limits + sandbox-exec for Ruflo** — kills R6 and R7 together; Ruflo sandbox profile prevents the attack vector that most plausibly compromises the wallet.
3. **Lazy-load MLX hot set + disk rotation policy + external heartbeat** — kills R5, R8, R10 and covers the "silent operational death" class of failures.

**Unmitigated residual:**
- No formal load test of 36GB colocation exists; R5 probability is a guess.
- Conway wallet is still single-key; cold split is aspirational until operator sets it up.
- Ruflo sandbox profile does not yet exist.
- Cutover playbook currently documents all-at-once flip; per-daemon canary replacement is aspirational.

**Recommendation:** DO NOT LAUNCH until R1, R2, R6, R7 are mitigated. R3, R4, R5, R8, R9, R10 need runbooks and monitoring before launch but are not strict blockers if runbooks exist.
