# AEGIS Phase 2 — Deep Domain Audits

8 specialist agent reviews, consolidated. Each finding includes severity, evidence, blast radius, and confidence.

Severity scale: **P0** (blocks ship) · **P1** (must-fix this week) · **P2** (next sprint) · **P3** (backlog) · **INFO**

---

## Domain 00 — Context & Intent (architect)

### F-00-001 — Worktree based on stale `shared/llm_client.py` (P0)
- **Evidence:** `wc -l shared/llm_client.py` → 257 lines in worktree. Audit instructions confirm main repo has 1711 lines. Worktree is the old Claude-direct + Ollama implementation; main has the LiteLLM tier routing. None of the 3 audit commits touch this file.
- **Impact:** Phase 42.5 v2 modules (`tiers.py`, `tier_classifier.py`, `semantic_cache.py`, `lead_worker.py`) are designed to be called BY a tier-aware llm_client. The worktree's llm_client knows nothing about tiers — it only understands `"fast"|"smart"|"genius"|"local"`. Wiring tests against this branch will hit the old code path.
- **Blast radius:** Total — every daemon imports `from shared.llm_client import llm`.
- **Action:** Before merging this branch, REBASE on current `main` so the new llm_client.py from main is the base, then re-apply Phase 42.5 v2 deltas. Or cherry-pick the 50 new files onto a fresh branch from main.
- **Confidence:** evidence_diversity=high · interpretation=high · impact=high · likelihood=high

---

## Domain 01 — Architecture & System Design (architect)

### F-01-001 — Phase 42.5 v2 modules are not wired into any daemon (P0)
- **Evidence:** Grep across `perseus titan hermes clawdbot conway deerflow_research ruflo openjarvis` for `from shared.tiers`, `from shared.tier_classifier`, `from shared.semantic_cache`, `from shared.lead_worker`, `from shared.spend_alerts`, `from shared.aider`, `from shared.voice`, `from shared.imagegen`, `from shared.verifier`, `from shared.escalation_log` → **zero matches**. Only `tests/` import these modules.
- **Impact:** ~9000 LOC of new infrastructure exists in `shared/` but is dead code from the daemons' perspective. Cutover plan calls for "FLIP" but there is nothing to flip — the daemons will continue calling the old `shared.llm_client.llm.generate(model="fast|smart|genius")` path.
- **Action:** Phase 42.5 v2 needs an integration phase: every daemon's LLM call site must be replaced with a tier-aware shim. Estimated ~30-50 call sites across 8 daemons.
- **Confidence:** evidence_diversity=high · interpretation=high · impact=high · likelihood=high

### F-01-002 — Tier registry has 11 tiers but no daemon-specific defaults documented in code (P2)
- **Evidence:** `shared/tiers.py:30-50` defines 11 TierName enum members. `litellm_config.yaml` has 28 model entries. No daemon→default-tier mapping table exists in code; instead it lives only in tier_classifier rules cascading on `daemon_matches`. Adding a new daemon = guess-and-check.
- **Action:** Add a `DEFAULT_TIER_BY_DAEMON: dict[str, TierName]` constant in `tiers.py`.
- **Confidence:** evidence_diversity=high · interpretation=medium

### F-01-003 — Fallback chains can route LOCAL_HEAVY → GENIUS silently (P1)
- **Evidence:** `shared/tiers.py:339` — LOCAL_HEAVY's `fallback_chain=[GENIUS, SMART]`. Comment notes this is intentional until T9 NVMe arrives, but a "free local" call invisibly turns into a $25/M-output Opus call. There is no log warning at the call site, only `logger.warning` inside `downgrade_tier()` which is unrelated.
- **Action:** Add structured fallback-event emission whenever LOCAL_HEAVY is invoked, so spend dashboard shows "X% of LOCAL_HEAVY calls routed to GENIUS this week".
- **Confidence:** medium-high

---

## Domain 02 — Data & State Integrity (data-engineer)

### F-02-001 — Migrations 046 and 047 not applied (P0)
- **Evidence:** `scripts/migrations/046-tier-spend-tracking.sql` creates `tier_spend_log`, `daemon_budget_caps`, `daily_spend_summary`, plus 4 views. `047-shadow-diffs.sql` creates `llm_shadow_diffs` and `shadow_diff_summary`. Both are version-controlled but `Makefile` migration target won't run them automatically. `shared/spend_alerts.py:74-80` queries `tier_spend_log` and `daemon_budget_caps` — if a daemon happens to import this it will throw `relation does not exist`.
- **Action:** Apply migrations during the BUILD step of cutover, before any daemon restart.
- **Confidence:** high

### F-02-002 — `daemon_budget_caps` seed inconsistent with `litellm_config.yaml` (P2)
- **Evidence:** Migration 046 lines 131-140 seed daemon caps. `litellm_config.yaml` `team_budgets` (lines 257+) contain the SAME caps. Two sources of truth — they happen to match today (titan 90, clawdbot 80, etc.) but will drift.
- **Action:** Either treat `litellm_config.yaml` as the source and seed migration via Makefile runtime substitution, or document the duplication.
- **Confidence:** high

### F-02-003 — `daemon_budget_caps` ON CONFLICT DO NOTHING means cap updates require manual SQL (P2)
- **Evidence:** Migration 046 line 140 — `ON CONFLICT (daemon) DO NOTHING`. Re-running the migration will not update caps if the rows already exist. This is correct for idempotency but means cap changes are not migration-driven.
- **Action:** Add a separate `update_daemon_caps.sql` runbook in `docs/runbooks/`.
- **Confidence:** medium

### F-02-004 — `tier_spend_log` has no retention policy (P2)
- **Evidence:** Table grows unbounded. Index on `timestamp DESC` will help reads but nothing prunes the table. At ~10k rows/day (rough estimate of LLM calls across 8 daemons) the table hits 1M rows in ~3 months.
- **Action:** Add `pg_cron` partition or weekly DELETE job in Perseus scheduler.

---

## Domain 04 — Security (security-engineer)

### F-04-001 — `litellm/sandboxes/verifier.sb` has never been validated (P0)
- **Evidence:** macOS sandbox-exec profile, 134 lines. References `tests/test_verifier_sandbox_red_team.py` which **does not exist** in the worktree (`ls tests/ | grep sandbox` returns nothing). The profile uses `(deny default)` followed by selective allows, which is the right shape, but a single syntax error in a `.sb` file causes the entire profile to fail open or fail closed depending on how it's loaded — and we don't know which because it's never been loaded.
- **Specific concerns:**
  - Line 84-95: `(deny ... (subpath "~/.ssh"))` — sandbox-exec does NOT expand `~`. This deny is INERT. The `~/.ssh` path is literal, not the user's home. **This is a real bug.** Need `(home-subpath ".ssh")` or absolute paths.
  - Line 105-107: `(deny file-write* (subpath "/Users/majovega/Desktop/Projects/objective-hertz") ...)` — hardcoded operator path; will not protect a Studio install at `/opt/perseus/repo`.
  - Line 71-74: writable scratch is `/tmp/perseus_verifier_scratch` — fine, but no rule prevents the verifier from following symlinks out of the scratch dir. Need `(deny file-link)` or `O_NOFOLLOW`.
- **Action:** Write the missing `tests/test_verifier_sandbox_red_team.py` and run it. Fix the `~` expansion bug. Replace operator-specific paths with env vars resolved at sandbox-exec invocation time.
- **Confidence:** evidence_diversity=high · interpretation=high · impact=critical · likelihood=high

### F-04-002 — Escalation log redactor regex coverage gaps (P1)
- **Evidence:** `shared/escalation_log/redactor.py:40-82` defines 15 regex patterns. Missing:
  - Slack tokens (`xox[abp]-...`)
  - Linear/Notion API tokens (`secret_...`, `lin_api_...`)
  - Base64-encoded auth headers without `Bearer` prefix
  - Postgres connection strings (`postgresql://user:pass@host/db`)
  - Generic high-entropy line at line 57 (`[A-Za-z0-9+/]{40,}={0,2}`) is too aggressive — it will redact JWT signatures (good) but also redact long log file IDs, hash digests, base58 wallet addresses (bad). Will produce false positives that destroy log readability.
- **Action:** Add missing patterns. Replace the generic base64 sweep with per-context patterns. Add the canary test as a unit test, not just runtime.
- **Confidence:** high

### F-04-003 — Semantic cache fetches OpenAI embeddings with no rate limit / no failure isolation (P2)
- **Evidence:** `shared/semantic_cache.py:240-251` instantiates a fresh `AsyncOpenAI` client every call. No retry, no circuit breaker, no per-daemon budget cap. If a daemon does 10k cacheable lookups in a tight loop, OpenAI bills accordingly with no spend_alerts integration.
- **Action:** Singleton the OpenAI client. Hook embedding calls into `tier_spend_log` with `tier='embedding'`.

### F-04-004 — `OPENAI_API_KEY` read from raw env without validation (P2)
- **Evidence:** `shared/semantic_cache.py:246` `client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))`. If the env var is unset, `api_key=None` is passed and the OpenAI SDK falls back to its own env-var lookup or fails on first call. Either way, no early failure.
- **Action:** `if not os.environ.get("OPENAI_API_KEY"): raise RuntimeError("...")` at module top OR check at first use.

### F-04-005 — Voice intent router can dispatch destructive daemon actions via voice (P1)
- **Evidence:** `shared/voice/intent_router.py:42-46` declares `pause_outreach`, `restart_daemon`, `pause_daemon`. The prompt at line 61 says `If destructive, set needs_confirmation=true` — but enforcement depends on the LLM honoring its own classification. No code-level enforcement. Anyone in earshot can say "Hey Jarvis, restart titan" and the daemon will act if the LLM marked it `needs_confirmation=false`.
- **Action:** Maintain a CODE-LEVEL allowlist of always-confirm actions. Voice classifier output cannot override.

### F-04-006 — LiteLLM master key rotation undocumented (P2)
- **Evidence:** `config/litellm_config.yaml:253` `master_key: os.environ/LITELLM_MASTER_KEY`. No rotation runbook. No "key changed at" audit. If the master key leaks, every per-daemon virtual key is implicitly compromised.
- **Action:** Document rotation in `docs/runbooks/litellm-key-rotation.md`.

---

## Domain 03 — Correctness & Logic (senior-app-engineer)

### F-03-001 — `shared/spend_alerts.py:143` uses deprecated `datetime.utcnow()` (P3)
- **Evidence:** `datetime.utcnow()` is deprecation-warned in Python 3.12 and removed in 3.13. Project requires Python 3.11 (pyproject.toml) so it works today, but the signal is a code freshness flag.
- **Action:** Use `datetime.now(timezone.utc)`.

### F-03-002 — Tier classifier "no signals at all" branch returns False from a matched rule (P1)
- **Evidence:** `shared/tier_classifier.py:327-330` — after rule iteration, if `not signals: return False, []`. But the very first check `if rule.operation_matches:` adds to signals only when match, otherwise returns early. The "no signals" branch is only reachable for the default rule (which is skipped with `continue` at line 251), so it's dead code. Not a bug, but confusing — the first rule with NO predicates would silently fail despite being conceptually a "match anything" rule.
- **Action:** Document or remove the dead-code branch.

### F-03-003 — `LeadWorkerLoop.run` sets `success` to True only when ALL steps succeed (P2)
- **Evidence:** `shared/lead_worker.py:192-193` `success = len(successful_steps) == len(all_step_results) and len(all_step_results) > 0`. But the loop has plan revision logic that re-plans on failure — meaning a step CAN fail and the loop can recover, yet the final result reports `success=False` because the failed step is still in `all_step_results`.
- **Action:** Track success at the goal level, not the step level. Or filter out steps that were superseded by re-planning.

### F-03-004 — `RuleBasedClassifier._matches` returns False if any predicate doesn't match (P2)
- **Evidence:** `shared/tier_classifier.py:289-309` — operation match required AND daemon match required AND keyword match required (when present). This is AND-logic across all configured predicates, but the documentation suggests OR-style "first match wins" cascading. Ambiguous.
- **Action:** Document explicitly that within a rule, predicates AND together; between rules, first match wins.

### F-03-005 — `SemanticCache._knn_search` similarity computation may be wrong (P2)
- **Evidence:** `shared/semantic_cache.py:272` `"score": 1.0 - float(doc.score)` — assumes Redis returns COSINE DISTANCE (so similarity = 1 - distance). Redis HNSW returns the metric configured at index creation time. The index is created with `DISTANCE_METRIC: COSINE` (line 221) which returns cosine DISTANCE in [0, 2], so `1 - distance` is wrong for distances > 1 (would yield negative similarity). For typical embeddings the distances stay in [0, 1] but the formula is mathematically incorrect.
- **Action:** Use `score = 1 - (distance / 2)` for normalized cosine, or switch to `IP` (inner product) and use raw score.

---

## Domain 06 — Testing Strategy (test-engineer)

### F-06-001 — Promised test file does not exist (P1)
- **Evidence:** `litellm/sandboxes/verifier.sb:14` says "Tested by: tests/test_verifier_sandbox_red_team.py". `ls tests/test_verifier_sandbox_red_team.py` → No such file. The whole sandbox layer has no validation.
- **Action:** Write the test (it MUST run sandbox-exec for real, not just parse the profile).

### F-06-002 — Spike scripts have no test coverage and no exit-code contract (P2)
- **Evidence:** `scripts/spikes/run_airllm_spike.py`, `run_gbnf_spike.py`, `run_litellm_hook_spike.py` — 798 LOC of exploration code. Per RUN.md these "haven't been run". No CI gate prevents them from being imported by mistake.
- **Action:** Move to `scripts/spikes/` with a `README.md` warning, or delete after spike conclusions are documented.

### F-06-003 — Phase 4x test files import directly without conftest fixtures (P3)
- **Evidence:** `tests/test_phase43_classifier.py:11-12` imports inside test functions (`from shared.tier_classifier import ...`) — a workaround for missing dependencies. Brittle: tests pass when modules don't exist.
- **Action:** Move imports to top-level. Use `pytest.importorskip` if optional deps are intended.

---

## Domain 07 — Reliability & Resilience (sre)

### F-07-001 — Migration `046` and `047` are not in any apply-on-boot path (P0)
- Same evidence as F-02-001 but viewed as a deploy-time issue. The cutover playbook doesn't explicitly call `make migrate` before flipping daemons.
- **Action:** Add `make migrate` as the first step of the FLIP phase in `docs/runbooks/cutover-playbook.md`.

### F-07-002 — No rollback test (P1)
- **Evidence:** `scripts/rollback_litellm.py` exists (172 LOC) but has never been executed in the worktree. `docs/runbooks/local-tier-rollback.md` documents the playbook. Neither has a CI test.
- **Action:** Add `pytest tests/test_rollback_dry_run.py` that imports rollback_litellm in dry-run mode and asserts no exceptions.

### F-07-003 — `LeadWorkerLoop` has no max wall-clock timeout (P2)
- **Evidence:** `shared/lead_worker.py:96-207` — `max_steps=20` bounds step count, but each step can hang on `execute_fn`. No `asyncio.wait_for` wrapper. A stuck Aider editor call hangs the loop forever.
- **Action:** Add `total_timeout_s` parameter, default 600s, enforced via `asyncio.wait_for`.

### F-07-004 — `check_daemon_spend` does no row-level locking on `tier_spend_log` (P3)
- **Evidence:** `shared/spend_alerts.py:54-95` — read-only queries, no FOR UPDATE. Concurrent spend recording from multiple daemons is fine, but the threshold-crossing logic is a TOCTOU race: daemon A and daemon B both query "spent < 90% cap", both proceed, both record spend, both push the daemon over 100%. No alert fires twice but the BLOCK action might not engage.
- **Action:** Move BLOCK enforcement to a DB CHECK or trigger.

---

## Domain 08 — Scalability & Performance (performance-engineer)

### F-08-001 — Semantic cache embeds every prompt before checking the allowlist (NO — actually correct)
- Re-read of `semantic_cache.py:108-130`: allowlist check is BEFORE `_embed`. This is fine. (Removing as a non-finding.)

### F-08-002 — `text-embedding-3-small` embedding for every cacheable prompt has hidden cost (P2)
- **Evidence:** `shared/semantic_cache.py:11-13` claims "$0.02/M tokens" — true. But every cacheable call pays embedding cost on miss AND on hit. For a 1k-token prompt, that's $0.00002 per check. At 100k prompts/day → ~$2/day → $60/month JUST for embedding lookups, not counting the saved LLM cost. Documented savings of "30-50% on cacheable ops" must net this overhead.
- **Action:** Add embedding cost line to `shared/spend_alerts.py` budget tracking.

### F-08-003 — `RuleBasedClassifier` re-compiles regex on every classify call (P2)
- **Evidence:** `shared/tier_classifier.py:303` `re.search(pattern, prompt_lower, re.IGNORECASE)` — Python's re module caches the last 512 patterns globally so this happens to work, but it's a fragile dependency. If patterns are added beyond 512, perf degrades silently.
- **Action:** Pre-compile patterns at module import.

---

## Domain 09 — Maintainability & Code Health (senior-app-engineer)

### F-09-001 — Two parallel cost-tracking pipelines (legacy + new) (P1)
- **Evidence:** Old `shared/llm_client.py` has `_record_claude_spend` writing to `budget_tracking` table. New `shared/spend_alerts.py` reads `tier_spend_log`. Until cutover, BOTH must work. After cutover, the legacy table becomes orphaned but is still queried by Hermes web/insights and Conway ledger code.
- **Action:** Document the cutover ordering: (1) deploy new spend writer, (2) backfill `tier_spend_log` from `budget_tracking`, (3) flip readers, (4) deprecate legacy.

### F-09-002 — Aliases in `TierName.from_string` may collide (P3)
- **Evidence:** `shared/tiers.py:55-69` — alias dict has `"opus": "genius"`, `"sonnet": "smart"`, `"haiku": "fast"`. This is fine. But `"claude": "smart"` is opinionated; future Claude releases at a different cost tier will quietly resolve to "smart".
- **Action:** Document that aliases are TODAY's mapping, subject to change.

---

## Domain 10 — Operability & DX (sre)

### F-10-001 — `make migrate` target behavior unverified (P1)
- **Evidence:** `Makefile` exists but I did not verify it picks up migrations 046+047. No CI test.

### F-10-002 — Spike scripts in `scripts/spikes/` are runnable from main checkout (P2)
- **Evidence:** No `if __name__ == "__main__": print("DO NOT RUN")` guard. Anyone running `python scripts/spikes/run_airllm_spike.py` against a populated env will hit unknown side effects.
- **Action:** Add operator-confirm prompt at top of each spike script.

---

## Domain 11 — Change Risk & Evolvability (staff-engineer)

### F-11-001 — Single-commit monolithic introduction of 9000 LOC (P0)
- **Evidence:** `git show --stat 30770c0` → 50 files changed, +9017 −0. No incremental evolution; the entire Phase 42.5 v2 dropped in one commit. This makes bisect, blame, code review, and rollback significantly harder.
- **Action:** Going forward, split feature commits along subsystem boundaries (1 commit per package: tiers, classifier, semantic_cache, lead_worker, etc.). For this audit pass, the commit is already in — but the LESSON is enforce smaller commits.
- **Confidence:** high

### F-11-002 — Nothing in the new shared/ packages is used by daemons (re: F-01-001) (P0)
- **Action:** Same — block ship until daemons import the new modules at least once.

### F-11-003 — Worktree drift from main means merge conflicts likely (P0)
- **Evidence:** `shared/llm_client.py` 257 vs 1711 lines = >1400 line delta. When this branch merges back to main, conflict resolution will be high-stakes (security-sensitive code) and slow. Other files may have similar drift not yet checked.
- **Action:** Rebase the worktree onto current main BEFORE attempting merge. Re-run audit after rebase.

---

## Domain 12 — Team & Knowledge Risk (staff-engineer)

### F-12-001 — Bus factor of 1 for entire Phase 42.5 v2 (P1)
- **Evidence:** All 3 commits from `nicoreincarnate-oss`, all in one evening, all co-authored "Claude Opus 4.6". One operator + one AI session is the only knowledge holder of why each design decision was made. `.paul/HANDOFF.md` partially mitigates but is itself 425 lines of unstructured prose.
- **Action:** Convert `.paul/HANDOFF.md` into structured ADR files, one per design decision.

### F-12-002 — Memory file `project_local_tier_phase_42_5_v2.md` referenced but its location is operator-private (P2)
- Operator's CLAUDE.md references this memory in `~/.claude/projects/-Users-majovega-...` — not in repo. New team members cannot read it.
- **Action:** Mirror critical memories into `docs/decisions/` with redaction.

---

## Phase 2 Summary

| Domain | Findings | P0 | P1 | P2 | P3 |
|--------|---------:|---:|---:|---:|---:|
| 00 Context | 1 | 1 | 0 | 0 | 0 |
| 01 Architecture | 3 | 1 | 1 | 1 | 0 |
| 02 Data | 4 | 1 | 0 | 3 | 0 |
| 03 Correctness | 5 | 0 | 1 | 3 | 1 |
| 04 Security | 6 | 1 | 2 | 3 | 0 |
| 06 Testing | 3 | 0 | 1 | 1 | 1 |
| 07 Reliability | 4 | 1 | 1 | 1 | 1 |
| 08 Performance | 2 | 0 | 0 | 2 | 0 |
| 09 Maintainability | 2 | 0 | 1 | 0 | 1 |
| 10 Operability | 2 | 0 | 1 | 1 | 0 |
| 11 Change Risk | 3 | 3 | 0 | 0 | 0 |
| 12 Team Risk | 2 | 0 | 1 | 1 | 0 |
| **Totals** | **37** | **8** | **8** | **15** | **3** |

(Domain 05 Compliance, Domain 13 Risk Synthesis — no findings in scope at this audit depth.)

P0 finding IDs: F-00-001, F-01-001, F-02-001, F-04-001, F-07-001, F-11-001, F-11-002, F-11-003.
