# Perseus Roadmap

## Active Milestone: v0.42.5 — Local Tier Hardening

Source of truth: `~/.claude/projects/-Users-majovega-Desktop-Projects-objective-hertz/memory/project_local_tier_phase_42_5_v2.md`

Phase 42.5 v2 is split into 5 PAUL plans within the `42-5-local-tier-hardening` phase directory because the full phase has 6 substep groups and PAUL targets 2-3 tasks per plan.

### Phase 42.5 Plans

| Plan | Focus | Status | Track |
|---|---|---|---|
| 42-5-01 | Foundation Validation — license memo + Perseus metadata plumbing + 2 spikes (GBNF×mlx_vlm, pre_call_hook rewrite) | Planning | research |
| 42-5-02 | Security Specs — macOS sandbox-exec profile + escalation log redaction pipeline | Not started | execute |
| 42-5-03 | Provisioning + Runtime — Studio setup, models, mlx_lm.server stack, launchd, /healthz, /metrics, rollback runbook | Not started | execute |
| 42-5-04 | Verifier + Aider + Voice + Image — 4-layer verifier with daemon-side L3 callback, Aider loops for Ruflo/Clawdbot, Parakeet+Kokoro voice loop, Draw Things image gen | Not started | execute |
| 42-5-05 | Golden Eval + Shadow Mode + Rollback Drill — 400 prompts captured, 72h shadow with $100 pool, drill the rollback runbook | Not started | execute |
| 42-5-06 | Cutover — risk-front-loaded daemon migration (Perseus → Hermes → Ruflo soak → Deerflow → Openjarvis → Conway → Titan 1-6 → Clawdbot) | Not started | execute |

Plan 42-5-01 is the **gating plan** — if any of its 4 validations fail, the entire Phase 42.5 v2 architecture changes and downstream plans must be revised before they're written.

### Prerequisites (must land BEFORE Phase 42.5 starts)
- Phase 40: LiteLLM proxy gateway live
- Phase 41: Tiered routing + budget controller live
- Phase 42: Langfuse observability + LiteLLM logs emitting tier tags
- 2TB Samsung T9 / OWC Envoy Pro SX / Acasis NVMe ordered and delivered

### Launch posture (locked 2026-04-07)
**Quality > launch date.** Perseus does not launch until the full Phase 42.5 compound stack is validated and cutover. The first Perseus customer interaction lands on the fully validated stack (85-90% Opus effective quality with 4-layer verifier and cloud safety net), NOT on a rushed cloud-only intermediate. Target 6-8 weeks total before first customer touches the system. No parallel-track engineering, no half-shipped state.

### Exit Criteria (14 gates, all must pass before Phase 43 starts)
1. ≥70% LiteLLM calls served by `local-mlx/*` over 7-day rolling window
2. ≥85% Layer 3 task success per daemon (no daemon <75%)
3. ≥88% behavioral divergence on frozen 200-prompt eval set, Sonnet-pinned judge
4. p50 ≤1.5s, p95 ≤5s for 1k-token prompts on n=1 path; p50 ≤4s on n=3 path
5. Zero OOM, MLX RSS ≤28 GB, daemon RSS ≤6 GB, no jetsam, no critical memory pressure
6. Production cost ≤$350/mo (shadow ≤$100/mo separately)
7. Escalation rate ≤25% (quality wins over cost)
8. Chain truncations <1% per daemon
9. Golden prompt regression: zero silent diffs vs locked outputs
10. Shadow completed within shadow_pool $100
11. License memo signed off (Qwen3 Apache 2.0, Qwen3-VL Apache 2.0, Parakeet, Kokoro, Draw Things)
12. Verifier L3 sandbox passes red-team test (file exfil + network egress blocked)
13. Escalation log canary test passes (100% synthetic secrets redacted)
14. Rollback drill completes in ≤1 hour from cold start
