# Pre-Launch Tech Debt Audit — Perseus Phase 42.5 v2

**Date:** 2026-04-07
**Auditor:** engineering:tech-debt skill
**Scope:** Whole Perseus codebase at `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/` — 8 daemons + 50 new Phase 42.5 v2 files (118 files / 15,352 LOC delta from main)
**Posture:** Operator wants the truth. Quality > launch date. No shrink-to-minimal.

---

## Executive Summary

**Total debt items identified: 27**

| Category | Count | Approx. effort |
|---|---|---|
| **Critical (blocks launch)** | 6 | ~14 days |
| **High (fix in week 1)** | 8 | ~12 days |
| **Medium (fix in month 1)** | 9 | ~10 days |
| **Low (backlog)** | 4 | ~6 days |

**Single biggest risk:** the worktree branched off a stale `shared/llm_client.py` (257 lines vs main's 1711 lines — 85% of the production client is missing). Merging Phase 42.5 v2 into main without rebasing this file will silently delete budget-aware routing, retry logic, semantic cache, and provider failover. This is the #1 launch blocker.

**Second biggest risk:** the new tier system (`shared/tiers.py`, 494 lines, `shared/tier_classifier.py`, 451 lines) exists, but the **LiteLLMBackend class that the tier system points to does not exist anywhere in `shared/`**. Tests reference `test_phase40_litellm_backend.py` (143 lines), but the class under test is unimplemented. Every daemon that asks for a tier today will crash at runtime.

---

## Confirmed shortcuts and missing pieces (operator's 10-item list, verified)

| # | Item | Status in worktree | Severity |
|---|------|-------------------|----------|
| 1 | Shortcuts in new code needing later cleanup | Multiple — see TD-101..104 | High |
| 2 | AirLLM heavy tier deferred until T9 NVMe | Confirmed deferred. $30-50/mo cloud overflow until ~week 6-8 | Accepted |
| 3 | Daemon-side L3 verifier registrations TODO | Confirmed — `register_verifier()` defined in `shared/verifier/a2a_callback.py:110` but **zero callers** across all 8 daemons | Critical |
| 4 | LiteLLMBackend class not written | Confirmed — no `class LiteLLMBackend` exists in `shared/`. Only `tiers.py` + `tier_classifier.py` are present | Critical |
| 5 | Ruflo sandbox subprocess wrapper missing | Confirmed — `shared/aider/ruflo_loop.py` (383 lines) has no sandboxed exec wrapper. `litellm/sandboxes/verifier.sb` exists but no Python wrapper invokes it | Critical |
| 6 | Golden prompt regression set not captured | Confirmed — `tests/test_phase44_regression.py` (289 lines) is structural; no real prompt corpus checked in | High |
| 7 | Shadow mode runner not implemented | Confirmed — `shared/verifier/depth_guard.py` references "shadow mode" tuning, but no `scripts/shadow_mode_runner.py` exists | Critical |
| 8 | Sandbox red-team test missing | Confirmed — no `tests/test_*redteam*.py` or `test_*red_team*.py` | High |
| 9 | docker-compose.yaml still exists alongside native plan | Confirmed — `./docker-compose.yaml` lives next to `./scripts/launchagents/` native services. Conflict risk during install | High |
| 10 | Worktree branched off stale `shared/llm_client.py` | Confirmed — 257 lines (worktree) vs 1711 lines (main). 1454 lines of production logic missing from this branch | Critical |

---

## Critical (blocks launch) — must fix before cutover

### TD-001 — `shared/llm_client.py` is 1454 lines behind main
- **File:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/llm_client.py`
- **Blast radius:** Every daemon, every LLM call, every budget decision
- **Effort:** 3 days (rebase + reconcile + re-test)
- **Impact 5 / Risk 5 / Effort 4 → Priority 20**
- **Fix order:** 1
- **Action:** `git fetch origin main` then 3-way merge `shared/llm_client.py` and reconcile with the new `shared/tiers.py` tier system. Run all `test_phase4*.py` after merge.

### TD-002 — `LiteLLMBackend` class does not exist
- **File:** missing — should live at `shared/llm_backends/litellm_backend.py` or in `shared/llm_client.py`
- **Blast radius:** All tier-routed calls. `tiers.py` says "actual model is configured in `config/litellm_config.yaml`" but nothing reads that config at runtime in shared/
- **Effort:** 2 days (write the class, wire to litellm proxy, plumb retries, costs, errors)
- **Impact 5 / Risk 5 / Effort 4 → Priority 20**
- **Fix order:** 2 (after TD-001 merge so we know which abstraction to extend)
- **Action:** Implement `class LiteLLMBackend` with `acompletion()`, `aembed()`, cost telemetry, fallback chain. Make `test_phase40_litellm_backend.py` actually run.

### TD-003 — Zero daemon-side L3 verifier registrations
- **File:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/verifier/a2a_callback.py:110`
- **Blast radius:** Verification layer is dead code until each of the 8 daemons calls `register_verifier(daemon, task_class, fn)` at boot
- **Effort:** 2 days (one verifier function per critical task class, 8 daemons × ~2 task classes each)
- **Impact 5 / Risk 5 / Effort 3 → Priority 30**
- **Fix order:** 3
- **Action:** In each daemon `main.py`, add a `register_verifiers()` call at startup. At minimum: titan/email_compose, titan/proposal_draft, hermes/alert_route, clawdbot/site_build, ruflo/code_fix, deerflow/research_score, conway/ledger_write, openjarvis/workflow_dispatch.

### TD-004 — Ruflo sandbox subprocess wrapper missing
- **File:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/aider/ruflo_loop.py`
- **Blast radius:** Ruflo executes generated code-fix patches **without macOS sandbox-exec isolation**. `litellm/sandboxes/verifier.sb` profile exists but is unused
- **Effort:** 1.5 days
- **Impact 5 / Risk 5 / Effort 3 → Priority 30**
- **Fix order:** 4
- **Action:** Add `shared/sandbox_exec.py` wrapper using `sandbox-exec -f litellm/sandboxes/ruflo.sb python -m …`. Route all Ruflo aider invocations through it.

### TD-005 — Shadow mode runner not implemented
- **File:** missing — should be `scripts/shadow_mode_runner.py`
- **Blast radius:** Cannot tune `depth_guard.py` caps, cannot A/B compare local-tier vs cloud, cannot validate quality before cutover
- **Effort:** 2 days
- **Impact 5 / Risk 4 / Effort 3 → Priority 27**
- **Fix order:** 5
- **Action:** Build a runner that fans every prod LLM call to (a) the current cloud path and (b) the new local tier in parallel, logs both, scores divergence, never touches user-visible output until thresholds pass.

### TD-006 — Sandbox red-team test missing
- **File:** missing — should be `tests/test_sandbox_redteam.py`
- **Blast radius:** No proof the sandbox profile actually contains a malicious code-fix patch. If the sandbox is permissive, Ruflo can write anywhere
- **Effort:** 1 day
- **Impact 5 / Risk 5 / Effort 2 → Priority 40**
- **Fix order:** 6 — this is the highest ROI critical item; do it first if possible
- **Action:** Test cases that try to write outside `/tmp/ruflo_*`, exfil env vars, network egress, fork-bomb. All must be blocked.

---

## High (fix in week 1) — ship-day-1 hygiene

### TD-101 — Golden prompt regression corpus not captured
- **File:** `tests/test_phase44_regression.py` (289 lines, structural only)
- **Effort:** 2 days. Capture 50-100 real prod prompts per critical pipeline stage.
- **Impact 4 / Risk 4 / Effort 3 → Priority 24** · Fix order 7

### TD-102 — `docker-compose.yaml` lives alongside native services plan
- **File:** `./docker-compose.yaml` + `./scripts/launchagents/`
- **Effort:** 0.5 day. Decide: delete compose file, OR move to `legacy/docker-compose.yaml`.
- **Impact 3 / Risk 4 / Effort 1 → Priority 35** · Fix order 8

### TD-103 — `shared/tiers.py` and `shared/tier_classifier.py` overlap unclearly
- 494 + 451 lines, two files, no architectural note in module docstrings stating which owns what.
- **Effort:** 0.5 day docs + light refactor. **Impact 3 / Risk 3 / Effort 1 → Priority 30** · Fix order 9

### TD-104 — `tmp_check_*.py` and `tmp_inspect_sites.py` in repo root
- Temp scripts checked in: `tmp_check_client_sites.py`, `tmp_check_tables.py`, `tmp_inspect_sites.py`
- **Effort:** 15 min. Move to `scripts/spikes/` or delete. **Impact 2 / Risk 2 / Effort 1 → Priority 20** · Fix order 10

### TD-105 — `shared/verifier/depth_guard.py` caps are theoretical placeholders
- Comments explicitly say "STARTING values from theoretical math. Phase 42.5 shadow mode" tunes them. Tied to TD-005.
- **Effort:** rolled into TD-005. **Priority 24** · Fix order 11

### TD-106 — Voice loop (Parakeet + Kokoro) has no end-to-end test
- 393 lines of new code in `shared/voice/` with no integration test
- **Effort:** 1 day. **Impact 3 / Risk 4 / Effort 2 → Priority 28** · Fix order 12

### TD-107 — Aider clawdbot loop has no failure-injection test
- `shared/aider/clawdbot_loop.py` (394 lines) — what happens if architect succeeds but editor fails? if patch is huge? if model returns malformed unified diff?
- **Effort:** 1 day. **Impact 4 / Risk 4 / Effort 3 → Priority 24** · Fix order 13

### TD-108 — `shared/spend_alerts.py` (189 lines new) — no alert delivery test
- Hermes integration not exercised in CI.
- **Effort:** 0.5 day. **Impact 3 / Risk 4 / Effort 2 → Priority 28** · Fix order 14

---

## Medium (fix in month 1)

### TD-201 — AirLLM heavy tier deferred until T9 NVMe ships
- **Cost:** $30-50/mo cloud overflow on Llama 70B class workloads for ~6-8 weeks. Operator-accepted. **Impact 3 / Risk 2 / Effort 5 → Priority 10** · Fix order 15
- **Action:** Calendar reminder for T9 arrival, kickoff plan ready in `project_local_tier_phase_42_5_v2.md`.

### TD-202 — `config/litellm_config.yaml` has no schema validator
**Effort 0.5 day · Priority 18 · Fix order 16**

### TD-203 — `shared/escalation_log/redactor.py` PII rules not unit tested
**Effort 0.5 day · Priority 20 · Fix order 17**

### TD-204 — `shared/verifier/grammar_compiler.py` (169 lines) — no fuzz test
**Effort 1 day · Priority 18 · Fix order 18**

### TD-205 — `shared/verifier/consistency.py` (185 lines) — N=3 self-consistency hardcoded
Should be tier-aware (cheap tier needs N=5, smart tier N=2). **Effort 0.5 day · Priority 16 · Fix order 19**

### TD-206 — `Perseus_Technical_Build_Sheet.docx` checked into repo
Binary in git history. Move to `docs/` or LFS. **Effort 0.5 day · Priority 12 · Fix order 20**

### TD-207 — `tools/browser-use/` is a vendored 3rd-party tree with its own LiteLLM
Two LiteLLM consumers in repo: `tools/browser-use/.../litellm/` and our new tier system. Diverging versions. **Effort 1.5 days. Priority 15 · Fix order 21**

### TD-208 — `shared/aider/__init__.py` only re-exports — no public API contract
**Effort 0.25 day · Priority 10 · Fix order 22**

### TD-209 — Migration scripts `migrate_to_litellm.py` + `rollback_litellm.py` untested
**Effort 1 day · Priority 16 · Fix order 23**

---

## Low (backlog)

### TD-301 — `shared/voice/intent_router.py` uses regex routing — should be model-based
**Effort 2 days · Priority 9 · Fix order 24**

### TD-302 — `shared/tier_classifier.py` decision tree is hand-rolled — replace with bandit
Tracked in openjarvis bandit_state. **Effort 3 days · Priority 8 · Fix order 25**

### TD-303 — `requirements.txt` and `pyproject.toml` both present — pick one
**Effort 0.5 day · Priority 8 · Fix order 26**

### TD-304 — Multiple `tmp_*.py` and `litellm/` top-level dir vs `shared/llm_*` naming inconsistency
**Effort 0.5 day · Priority 6 · Fix order 27**

---

## Top 5 by ROI (priority score)

| Rank | ID | Item | Score | Effort | Why now |
|------|-----|------|-------|--------|---------|
| 1 | TD-006 | Sandbox red-team test | 40 | 1 day | Highest ROI. Without this, Ruflo could rm the repo. Cheap to write. |
| 2 | TD-102 | Delete or relocate `docker-compose.yaml` | 35 | 0.5 day | Trivial cleanup, removes deploy ambiguity |
| 3 | TD-003 | Daemon-side L3 verifier registrations | 30 | 2 days | Verification layer is currently dead code |
| 4 | TD-004 | Ruflo sandbox wrapper | 30 | 1.5 days | Pairs with TD-006; together they harden Ruflo end-to-end |
| 5 | TD-103 | Tier system module boundaries doc | 30 | 0.5 day | Prevents the next contributor from making the divergence worse |

---

## Recommended fix order (compressed)

**Days 1-4 (critical path):**
1. TD-001 rebase `shared/llm_client.py` against main (3d)
2. TD-002 implement LiteLLMBackend (2d, parallel with #1's tail)

**Days 5-9 (security + verification):**
3. TD-006 sandbox red-team test (1d)
4. TD-004 sandbox wrapper (1.5d)
5. TD-003 verifier registrations across 8 daemons (2d)
6. TD-005 shadow mode runner (2d)

**Week 2:**
7-14. All remaining High items (TD-101 through TD-108)

**Month 1:**
15-23. Medium items in priority order

**Backlog:**
24-27. Low items as time permits

---

## Notes for the operator

- The `shared/llm_client.py` divergence (TD-001) is the only item that could quietly corrupt prod. Everything else is loud failure (crash, missing class, dead callback). Address TD-001 first because everything downstream depends on knowing what abstraction shape we're actually targeting.
- The 50 new Phase 42.5 v2 files are well-organized and well-named. The debt is concentrated in three places: (a) the rebase gap, (b) missing wiring (verifier registrations, sandbox wrapper), (c) missing test corpora (golden prompts, red-team). None of it is "bad code" — it is "unfinished plumbing", which is exactly what an honest mid-build audit should catch.
- Total critical-path effort to clear all blockers: **~14 working days** for one engineer, ~7 days with two engineers working in parallel on TD-001/002 vs TD-003/004/005/006. This fits inside the 6-8 week Phase 42.5 cutover window with significant margin.
- Operator-accepted debt (TD-201 AirLLM defer) is the only item the team should consciously carry past launch. Total monthly cost of carry: $30-50.
