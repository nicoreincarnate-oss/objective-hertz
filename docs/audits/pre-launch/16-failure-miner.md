# Pre-Launch Audit 16: Failure Miner — Historical Failure Patterns for Phase 42.5 v2

**Date:** 2026-04-07
**Skill:** failure-miner (manual analysis fallback)
**Scope:** Mine Perseus session history, git log, and planning directory for failure patterns predictive of Phase 42.5 v2 launch risk.

---

## Data Sources Inspected

- `/Users/majovega/Desktop/Projects/objective-hertz/.planning/phases/` — 50+ phase directories including historical rollbacks
- `/Users/majovega/Desktop/Projects/objective-hertz/` git log (440 commits across all branches)
- `/Users/majovega/.claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory/` — MEMORY.md and mega-plan files
- `/Users/majovega/Desktop/Projects/objective-hertz/.planning/phases/23-unified-llm-client/VERIFICATION.md` — the LiteLLM/unified factory precedent
- `/Users/majovega/Desktop/Projects/objective-hertz/.planning/phases/42-runtime-memory-architecture/` — PLAN + QA-CHECKLIST

Ruflo patterns table not queried (requires live DB). Session transcripts not directly accessible; compensated with git history.

---

## Top 5 Historical Failure Patterns Relevant to Phase 42.5 v2

### Pattern 1: Flag-Gated Rewrites Never Cut Over — "Dead Code by Default"
**Evidence:** Phase 23 unified LLM client shipped 10 new files, 1,500 LOC, 94 passing tests, three feature flags (`ANATOMY_UNIFIED_LLM`, `UNIFIED_LLM_FACTORY`, `UNIFIED_LLM_WITHHOLDING`), a shadow comparator, a 48h parallel validation gate — and then sat dormant. The VERIFICATION.md explicitly lists "Cutover decision when gate criteria met" as a Next Step that was never reached. Phase 40 (`40-litellm-gateway`) now exists as a do-over. Two distinct "Phase 42" directories exist (`42-observability-caching` and `42-runtime-memory-architecture`) — the numbering collision itself is evidence of plan churn.

**Why it predicts Phase 42.5 failure:** Phase 42.5 v2 calls for an all-at-once cutover to defeat exactly this failure mode, but the same organizational pressure (risk aversion, shadow comparators, "just a bit more validation") that stranded Phase 23 will recur.

**Predicted failure mode:** Cutover day arrives, the shadow comparator shows a 3-7% quality delta on some edge-case eval, the operator blinks, and Phase 42.5 joins Phase 23 in the dormant-rewrites graveyard. Six weeks of work, zero production impact.

**Mitigation:**
1. Pre-commit to a specific cutover date AND a specific rollback-only-if metric (e.g., "rollback only if eval delta > 15% OR daemon crash rate > 2x baseline"). No other number justifies delay.
2. Delete the old code paths in the same commit that enables the new paths. No feature flag for the cloud-only mode. The v1 mega-plan was explicitly superseded — that decisiveness must carry into the cutover.
3. Schedule a "kill switch meeting" T+24h post-cutover with only two outcomes: ratify or rollback. No third "keep watching" option.

---

### Pattern 2: Test Isolation Pollution Across Phase Boundaries
**Evidence:** Repeated commits fixing test isolation:
- `fix(phases 25-28): test fixes for Python 3.9 compat and test isolation`
- `fix(12-01): fix test isolation for cross-module DB mock compatibility`
- Phase 23 VERIFICATION.md explicitly notes: *"test_phase23_fallback_chain.py fails when run in the same process as test_phase23_unified_llm.py due to pre-existing sys.modules mock pollution between test files. Both pass when run individually."*

**Why it predicts Phase 42.5 failure:** Phase 42.5 v2 introduces 7+ new model backends (Qwen3-30B-A3B, Qwen-Coder-14B, Qwen-8B, Qwen-VL-7B/32B, Parakeet, Kokoro, embeddings) plus an AirLLM tier and Aider subprocess harness. Each one will want to monkeypatch `sys.modules`, HTTP clients, or subprocess for testing. The mock pollution problem will not go away — it will compound.

**Predicted failure mode:** CI passes locally per-file, fails in aggregate on first post-cutover merge. L3 verifier callback test suite is especially at risk because it crosses async + subprocess + HTTP mock boundaries.

**Mitigation:** Mandate `pytest --forked` or `pytest-xdist --dist=loadscope` for all Phase 42.5 test files touching provider backends. Add a CI job that runs the full Phase 42.5 test set in a single process as a pollution detector.

---

### Pattern 3: Crypto / PQC Integration Repeatedly Needed "Fix Commits" After Feature Landing
**Evidence:** Phase 6 PQC wallet integration:
- `feat(06-01): add HybridEncryptor — ML-KEM-768 + AES-256 hybrid encryption`
- `fix(06-01): resolve ruff lint warnings in PQC modules`
- `feat(06-02): wire PQC into Conway wallet with feature flag`
- `fix(06-02): fix wallet PQC test private key format (0x + 64 hex chars)`
- `feat(06-01): add PQC ARM64 validation spike script` — ARM64 validation was a *spike* added after the feature, not before.

**Why it predicts Phase 42.5 failure:** Phase 42.5 is ARM64-native (M4 Max). Any Rust/C++ dependency in the model stack (llama.cpp, mlx, AirLLM's CUDA shims, Parakeet FFI) has historically required a fix-up commit for ARM64 key formats, binary paths, or lint. The Conway wallet pattern — "ship feature → discover platform quirk → fix test fixtures" — will replay for every native model backend.

**Predicted failure mode:** Parakeet or Kokoro import works on dev laptop, fails in daemon subprocess because of missing `DYLD_LIBRARY_PATH` or codesigning for unsigned dylibs under the macOS sandbox profile.

**Mitigation:** Before cutover day, run every model backend *inside* the target sandbox profile (not just in bare shell) and capture the full crash trace. Do not assume "works in terminal" == "works under sandbox-exec".

---

### Pattern 4: Sandbox / Isolation Attempts Shipped Without Full Daemon Integration
**Evidence:**
- `feat(03-01): add per-daemon memory isolation via DNA profiles` — isolation added but required a separate `merge(03-01)` follow-up to actually wire the cache + migration
- `fix(16-03): emit_event docstring + close_pool _pool_lock fix (AEGIS)` — AEGIS middleware had to be patched *after* the daemon wiring commit because lock semantics were wrong
- `fix(aegis): wire middleware chain into Titan daemon — 6 layers active (F-DA-002)` — middleware existed for multiple commits before it was actually wired into the daemon startup path

**Why it predicts Phase 42.5 failure:** The macOS sandbox profile is conceptually similar — a layer that exists as code but must be correctly wired into daemon startup, subprocess spawns, AND the Aider architect+editor pattern (which spawns its own subprocess tree). Partial wiring is the norm, not the exception.

**Predicted failure mode:** Sandbox profile blocks model weight files on first daemon restart post-cutover because the profile grants `/Users/majovega/models` but Aider/llama.cpp `mmap`s from a symlink target in `/opt/homebrew/var/cache`. Silent failure: model loads but returns nonsense because of a partial read.

**Mitigation:** Before cutover, run the full Ruflo → Aider → editor → L3 verifier callback loop end-to-end inside the sandbox profile from a fresh daemon restart. Count zero "permission denied" AND zero "file not found" in `Console.app` as the gate. If you see even one, the profile is wrong.

---

### Pattern 5: Deferred Components Come Back as Scope Creep at the Worst Time
**Evidence:** Phase 42.5 v2 mega-plan explicitly defers AirLLM Llama 70B heavy tier. Historical precedent:
- `Phase 42.5: native services (Option B) + 1TB interim drive + AirLLM defer` — the defer was decided *during* execution, not during planning
- The v1 mega-plan (`project_local_tier_phase_42_5.md`) was superseded by v2 after a beta debate; Gemma 4 26B A4B design was dropped. That's a feature that made it to beta debate and still got cut.

**Why it predicts Phase 42.5 failure:** The deferred AirLLM tier is the release valve for "quality not good enough on the 30B tier." When the operator sees the compound system benchmarks at T-7 days and they land at 73% instead of 80%, the pressure to un-defer AirLLM will be immense. Un-deferring it mid-cutover means re-planning the sandbox profile, the routing layer, and the L3 verifier's tier-assignment logic — all in the final sprint.

**Predicted failure mode:** Week 5 of 6-8: benchmark disappointment → "let's just squeeze AirLLM in" → routing logic rewrite → test pollution (Pattern 2) → cutover slips → Phase 23 ghost returns (Pattern 1).

**Mitigation:** Lock the "AirLLM stays deferred unconditionally until T+30 days post-cutover" rule in the mega-plan NOW, and add it to Operator Preferences memory. Frame any benchmark disappointment as a prompt/routing problem, not a "we need more model" problem. If quality is actually unrecoverable at 30B, the correct response is to delay the *launch*, not to un-defer.

---

## Additional Lower-Severity Patterns

**P6 — Voice loop latency is a new failure surface:** No prior Perseus phase has shipped a real-time voice round-trip. Parakeet STT + Kokoro TTS + 8B inference must land under ~1.2s end-to-end or it feels broken. There's no historical baseline to debug against.

**P7 — Daemon-side callbacks love to deadlock:** `fix(16-03): _pool_lock fix` and `fix: wire memory bus subscriber and bandit persistence into daemon startup` both hint at ordering/lock problems in daemon startup. The L3 verifier callback is another daemon-side async primitive — expect at least one deadlock on first integration.

**P8 — Sessions 2/v18.1 had "wire all fixes + schema fixes" mega-commits** suggesting the team bundles too many fixes into single commits under time pressure. This makes rollback surgical-impossible. Phase 42.5 must commit each backend separately.

---

## Summary Table

| # | Pattern | Phase 42.5 v2 Component At Risk | Severity |
|---|---------|----------------------------------|----------|
| 1 | Flag-gated rewrites never cut over | All-at-once cutover | CRITICAL |
| 2 | Test isolation pollution | L3 verifier callback + provider tests | HIGH |
| 3 | ARM64 / native platform quirks | Parakeet, Kokoro, Aider subprocess | HIGH |
| 4 | Sandbox wired but not integrated | macOS sandbox profile | HIGH |
| 5 | Deferred scope returns as creep | AirLLM un-defer pressure | CRITICAL |
| 6 | Voice loop has no baseline | Voice loop latency | MEDIUM |
| 7 | Daemon callback ordering bugs | L3 verifier callback | MEDIUM |
| 8 | Mega-commits prevent surgical rollback | Cutover commit discipline | MEDIUM |

---

## Overall Launch Risk Assessment

The single largest historical risk is **Pattern 1 recurring**. Perseus has a documented pattern of landing ambitious rewrites behind feature flags and then never flipping the flag. Phase 42.5 v2 is explicitly designed to break this pattern with an all-at-once cutover — but the cultural muscle memory is the opposite, and the deferred AirLLM tier (Pattern 5) provides the perfect rationalization to delay.

**Recommended launch gate:** Before cutover day, confirm in writing (in the mega-plan) a pre-committed cutover date, a pre-committed rollback-only metric, and a pre-committed "AirLLM stays deferred" rule. Without all three, the probability of a Phase-23-style dormant landing is high.

---

**Report path:** `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/docs/audits/pre-launch/16-failure-miner.md`
