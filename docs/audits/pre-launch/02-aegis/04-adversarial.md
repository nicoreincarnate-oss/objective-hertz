# AEGIS Phase 4 — Adversarial Review (Devil's Advocate)

Challenges to the prior phases. The job here is to invalidate, not agree.

## Challenges to Phase 2-3 findings

### CHAL-01 — "9k LOC in one commit" is over-counted
**Challenged finding:** F-11-001 (P0)
The 9017 line count includes test files (982 LOC), runbooks (~1300 LOC), config YAML (445 LOC), spike scripts (798 LOC), and SQL migrations (221 LOC). The actual production-path Python is closer to **~3500 LOC across ~15 modules**, which is large but not exceptional for a phase commit. Downgrading from "P0 concentration risk" to "P2 review hygiene".
**Devil's verdict:** Severity claim partly invalid. The integration gap (F-01-001) is the real P0; the LOC count is downstream.

### CHAL-02 — "Modules not wired" might be intentional staging
**Challenged finding:** F-01-001 (P0)
What if the operator deliberately landed the new modules first as a safe-to-merge "library" change, with daemon wiring planned as the NEXT commit? The commit message for `30770c0` says "all autonomous-buildable artifacts" — meaning the things that can be built without operator review. Daemon wiring requires human review per the project rules.
**Devil's verdict:** Plausible. Reframe F-01-001 from "missing integration" to "integration commit not yet authored". The P0 stands because launch readiness still requires it, but the failure mode is "schedule slip" not "broken code".

### CHAL-03 — Verifier sandbox `~/.ssh` deny is partially defensible
**Challenged finding:** F-04-001
sandbox-exec on macOS does support `(home-subpath ".ssh")` syntax. The use of `(subpath "~/.ssh")` is a literal-path bug, but on macOS with the right loader, the sandbox could STILL block ssh access via a separate `(deny file-read* (literal "/Users/majovega/.ssh/..."))` rule. **However**, no such rule exists in the file. So the bug is real. **Verdict upheld.**

### CHAL-04 — Stale llm_client.py might not matter if no code path triggers
**Challenged finding:** F-00-001, F-CR-001
If the new modules are dead code (CHAL-02), then the stale llm_client doesn't actually break anything at runtime today. The drift only matters at MERGE time. So the severity is "future merge pain", not "broken now".
**Devil's verdict:** Accurate critique. Reframe as "P0 at merge time, P2 right now". But ship date is "days away" per Phase 0, so merge time IS right now. Severity stands.

### CHAL-05 — The audit missed: there is no rate limit on the Telegram alert path
**New finding raised by Devil:**
`shared/spend_alerts.py:dispatch_alert` will send a Telegram alert on every threshold-crossing. If a daemon's spend oscillates around 75%, it will send a WARNING alert every check cycle, every minute. Telegram's bot API rate limit is 30 messages/sec but only 20 messages/min to the same chat. **Operator gets paged once, then alerts get silently dropped by Telegram for the rest of the day.**
**Severity:** P1 (you lose alerting reliability silently).
**Logged as F-04-007.**

### CHAL-06 — The audit missed: `_evaluate_thresholds` returns ONE alert at the highest threshold
**New finding:**
`shared/spend_alerts.py:128-145` iterates THRESHOLDS in descending order and returns at the first match. This is correct behavior, but the test for it doesn't exist. Test gap.
**Severity:** P3.
**Logged as F-06-004.**

### CHAL-07 — The audit missed: spike scripts may auto-import on test discovery
**New finding:**
If pytest is configured with `testpaths` covering `scripts/`, pytest collection will import `scripts/spikes/run_airllm_spike.py`. The spike imports may have side effects (network calls, model downloads). Need to confirm pytest scope.
**Severity:** P2.
**Logged as F-06-005.**

### CHAL-08 — The audit missed: `LiteLLM master_key` leak via process listing
**New finding:**
`config/litellm_config.yaml` references `os.environ/LITELLM_MASTER_KEY`. The LiteLLM proxy process command line on macOS will be visible to any user via `ps -E` or `ps -e ww` on Linux. `LITELLM_MASTER_KEY=xyz litellm --config ...` exposes it.
**Mitigation:** Use a `.env` file LOADED by litellm, not env-var-on-command-line.
**Severity:** P2.
**Logged as F-04-008.**

### CHAL-09 — Suspicious consensus: every "P0" finding is integration-related
The audit found 8 P0s but they all cluster around "modules not wired" and "stale base". Could the audit be SUFFERING from anchoring on the worktree drift discovery? Other risk classes (DoS, data corruption, secret exfil at rest) have only P1s and below. Either the codebase is genuinely safe in those classes OR the audit didn't dig deep enough.
**Devil's verdict:** Partially true. Without Trivy/Semgrep/Gitleaks, the audit can't see CVE chains, taint flows, or git-history secret leaks. The P0 cluster is real but may be hiding sub-P0 issues in unscanned domains.

## Devil's New Findings Summary

| ID | Severity | Description |
|----|---------:|-------------|
| F-04-007 | P1 | Telegram alert rate-limit drop unhandled |
| F-04-008 | P2 | LiteLLM master key leakable via process list |
| F-06-004 | P3 | _evaluate_thresholds branch coverage gap |
| F-06-005 | P2 | pytest may auto-import spike scripts |

## Devil's Severity Recalibrations

- F-11-001: P0 → P2 (LOC count is mostly non-Python; integration is the real issue)
- F-00-001: stays P0 (drift is real and merge time is now)
- F-01-001: stays P0 (regardless of intent, ship requires it)
