# Phase 1: Audit Recovery - Context

**Gathered:** 2026-04-07 (overnight autonomous run)
**Status:** Ready for planning
**Mode:** Auto-generated (discuss skipped via workflow.skip_discuss=true)

<domain>
## Phase Boundary

Clear as many P0 and P1 findings from the pre-launch audit as possible overnight without requiring operator decisions. Produce a PORT-PLAN.md for the risky recovery items (P0-1, P0-2, P0-3) that need operator approval in the morning.

This phase is internally referred to as "Phase 42.5b" — the recovery sub-phase of the Phase 42.5 v2 Local Tier Hardening work whose pre-launch audit returned BLOCK LAUNCH with 12 P0s + 16 P1s.

The pre-launch audit pass found:
- **The big finding (P0-1)**: Worktree's `shared/llm_client.py` is 257 lines, main repo's is 1711 lines. The worktree branched off a stale version. Main already has tier routing, fallback chains, StickyLatch caching, budget gating with downgrade, watchdog timeouts per tier, and AirLLM heavy local integration. Phase 42.5 v2 is largely a duplicate of work that already exists in main.
- **P0-2**: Phase 42.5 v2 modules NOT WIRED to any daemon (ghost integration). `grep -r "from shared.tiers" perseus/ titan/ hermes/ clawdbot/ conway/ deerflow_research/ ruflo/ openjarvis/` returns ZERO matches.
- **P0-3**: Verifier sandbox is decorative — Ruflo Aider has `sandbox_runner=None` default, no Python wrapper invokes sandbox-exec, no red-team test exists.
- **P0-4 through P0-11**: Various security and quality issues across the new code (most already fixed in Wave R1).
- **P0-12**: 140+ outstanding UAT items across 10 phases.

The Phase 42.5 v2 implementation otherwise has high code quality (8.83/10 from quality-gate, 98% spec adherence from misalignment-detector) — the spec was just based on a stale code baseline.

</domain>

<decisions>
## Implementation Decisions

### Wave structure (already partially executed — see status)

The recovery is organized into 5 waves of work:

**Wave R1 — Quick-win P0s (already 7/8 landed before GSD took over)**:
- P0-4 sandbox tilde path expansion (commit `bd18ae9`)
- P0-5 scrypt KDF replacing unsalted SHA-256 in escalation log redactor (linter-applied)
- P0-6 crypto fail-closed in escalation log (linter-applied)
- P0-7 22 Phase 42.5 v2 env vars added to .env.example (commit `b0a9e90`)
- P0-9 self-consistency vote launder fix (commit `3ab29c1`)
- P0-10 grammar compiler raise on unknown JSON Schema features (linter-applied)
- P0-11 migrate_to_litellm.py multi-line AST fix (linter-applied)
- P0-8 4 missing launchd plists (Parakeet/Kokoro/Mem0/N8N) — REMAINING

**Wave R2 — Investigation (gates the recovery)**:
- Read main repo's `shared/llm_client.py` (1711 lines) in full
- Diff worktree's tier system vs main's existing implementation
- Survey all 8 daemons in main for current `llm.generate()` call sites
- Synthesize PORT-PLAN.md with concrete delete/port/refactor sequencing

**Wave R3 — Operational P1s (parallel)**:
- P1-1 stub files for spec'd-but-missing artifacts
- P1-2 5 missing redactor patterns (OpenRouter, Telegram, Slack, ETH private key, Postgres conn string)
- P1-7 LeadWorkerLoop context dict mutation deep copy
- P1-9 escalation log file rotation script
- P1-13 6 missing daemon launchd plists with healthchecks
- P1-15 hermes/web/app.py DASHBOARD_HOST default 0.0.0.0 → 127.0.0.1 (if file in worktree)

**Wave R4 — Verification**:
- Run pytest on `tests/test_verifier_layers.py`, `tests/test_phase42_semantic_cache.py`, `tests/test_phase41_tiers.py`
- Import spot checks for the Wave R1 fixes
- Update `docs/audits/pre-launch/SUMMARY.md` with REMEDIATED section listing cleared P0s/P1s + commit SHAs

**Wave R5 — Handoff**:
- Update `.paul/HANDOFF.md` with OVERNIGHT RECOVERY STATUS section at top
- Update `.planning/STATE.md` to reflect Phase 1 completion or in-progress status
- git push for offsite backup

### NOT in scope (operator decision required after PORT-PLAN.md review)

- P0-1 EXECUTION: porting modules to main's existing tier system (8h work, requires architectural decisions)
- P0-2 EXECUTION: wiring daemons to new modules (depends on P0-1 outcome)
- P0-3 EXECUTION: Ruflo sandbox subprocess wrapper (depends on operator decision: subprocess vs daemon)

These wait for operator approval after reading PORT-PLAN.md in the morning.

### Atomicity

Atomic commits per fix. Wave R3 fixes can run in parallel via Agent subagents (proven pattern from the audit pass and Wave R1).

</decisions>

<code_context>
## Existing Code Insights

The GSD plan-phase agent will gather codebase context. Pre-existing context worth surfacing:

- **`shared/llm_client.py` (worktree, 257 lines)**: stale, missing 1454 lines that exist in main. The new Phase 42.5 v2 modules (`shared/tiers.py`, `shared/semantic_cache.py`, `shared/tier_classifier.py`, `shared/lead_worker.py`, `shared/aider/`, `shared/voice/`, `shared/imagegen/`, `shared/verifier/`, `shared/escalation_log/`, `shared/spend_alerts.py`) all reference an `llm.generate(tier=, operation=, daemon_name=)` signature that does not exist in either the worktree's 257-line client OR main's 1711-line client.
- **`shared/escalation_log/redactor.py`**: Already received P0-5 + P0-6 fixes (scrypt KDF + fail-closed). Wave R3 P1-2 will add 5 more redaction patterns to it.
- **`shared/verifier/consistency.py`**: Already received P0-9 fix (failure laundering). Has new `attempted_samples` and `successful_samples` fields on `ConsistencyResult`.
- **`shared/verifier/grammar_compiler.py`**: Already received P0-10 fix (raises `UnsupportedSchemaFeatureError` instead of silently degrading to "string").
- **`scripts/migrate_to_litellm.py`**: Already received P0-11 fix (uses AST `end_lineno` to detect multi-line calls, defers them to manual review).
- **`litellm/sandboxes/verifier.sb`**: Already received P0-4 fix (uses `(string-append (param "HOME") "/...")` instead of literal `~/...`).
- **`scripts/launchagents/`**: Currently has plists for `master`, `titan`, `clawdbot`, `dashboard`, `frontend`, `ruflo`, `backup`, `browser-use`. Missing: `parakeet`, `kokoro`, `mem0`, `n8n`, `hermes`, `conway`, `deerflow`, `openjarvis`. P0-8 covers the first 4, P1-13 covers the rest.

</code_context>

<specifics>
## Specific Ideas

### Wave R2 PORT-PLAN.md structure

The most critical artifact this phase produces. Operator reads it in the morning to decide the recovery path. Must contain:

1. **Files to DELETE** (worktree files redundant with main): explicit list with rationale
2. **Files to PORT** (genuinely net-new modules): explicit list, target main location
3. **Files to REFACTOR** (need adaptation to fit main's existing tier system): explicit list, refactor sketch
4. **Daemon wiring changes** (per daemon: which call sites need to import which new modules)
5. **Estimated effort** per item in hours
6. **Recommended sequencing** (what to do first, what depends on what)
7. **Two-engineer split option** (which items can run in parallel)
8. **Risk-front-loaded option** (highest blast radius items first)

### Wave R1 P0-8 launchd plists

Match the style of existing plists in `scripts/launchagents/`. Each plist should:
- Have a `Label` matching `com.perseus.{name}`
- `RunAtLoad: true`, `KeepAlive: true`
- Bind to localhost on the documented port (Parakeet 11440, Kokoro 11441, Mem0 8888, N8N 5678)
- Standard out/error to `~/Library/Logs/perseus/{name}.log`
- Working directory under `/Users/majovega/Desktop/Projects/objective-hertz` or `/opt/perseus`

### Wave R3 P1-13 daemon plists

The 6 missing daemon plists (hermes, conway, deerflow, ruflo-already-exists?, openjarvis, clawdbot-health) should each:
- Reference the correct `python -m {daemon}.daemon` startup command
- Include `EnvironmentVariables` block sourcing from `.env`
- Include `ExitTimeOut` (was flagged as P1-10)
- Wire healthcheck via the daemon's existing /healthz or A2A endpoint

</specifics>

<deferred>
## Deferred Ideas

- P0-1, P0-2, P0-3 EXECUTION: gated on operator review of PORT-PLAN.md in the morning
- F5-TTS voice cloning: backlog (operator already noted this for post-Kokoro evaluation)
- AirLLM heavy tier wiring: deferred until Samsung T9 NVMe arrives (1-2 weeks)
- Recraft API and OpenRouter API key setup: operator action, expected tomorrow

</deferred>
