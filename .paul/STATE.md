# PAUL State

## Current Position

Milestone: v0.42.5 Local Tier Hardening
Phase: 42.5 of 44 (Local Tier Hardening) — Planning
Plan: 42-5-01 created, awaiting approval
Status: PLAN created, ready for APPLY
Last activity: 2026-04-07 — Created `.paul/phases/42-5-local-tier-hardening/42-5-01-PLAN.md`

Progress:
- Milestone v0.42.5: [░░░░░░░░░░] 0%
- Phase 42.5: [░░░░░░░░░░] 0%
- Plan 42-5-01 of 6: created, awaiting approval

## Loop Position

```
PLAN ──▶ APPLY ──▶ UNIFY
  ✓        ○        ○     [Plan 42-5-01 created, awaiting approval]
```

## Decisions

| Date | Decision | Rationale |
|---|---|---|
| 2026-04-07 | Use Qwen3-30B-A3B as primary local generalist (replaces Gemma 4 26B A4B) | Better quality, native MLX tool calls (no patch), same speed, kills mlx-lm fork dependency |
| 2026-04-07 | Compound system architecture (local + 4-layer verifier + cloud safety net) | Pure-local 36GB cannot hit 80-90% Opus quality; compound system math: 0.70 × 0.75 + 0.70 × 0.25 × 0.95 + 0.30 × 0.95 ≈ 85-90% effective |
| 2026-04-07 | Aider architect+editor pattern for Ruflo and Clawdbot | Opus plans (5% of calls), local Coder edits (95%), pytest verifies. Strictly better than pure-local code-modifying daemons |
| 2026-04-07 | Voice loop in scope (Parakeet + Kokoro) | Free upgrade, 0.6 GB resident, sub-second offline voice for Hermes/Jarvis |
| 2026-04-07 | Image gen via Draw Things in scope for Clawdbot | Free local iteration drafts, Recraft stays for production assets per CARL decision |
| 2026-04-07 | Plan 42-5-01 is gating — failure blocks all downstream plans | If GBNF×mlx_vlm spike fails or pre_call_hook lacks rewrite, architecture changes |
| 2026-04-07 | **Quality > launch date — Perseus does NOT launch until Phase 42.5 is fully cutover.** Earlier "launch in 2 days" framing is REVOKED. Target 6-8 weeks total before first customer interaction. | Operator decided "the launch does not take priority. what takes priority is it being the best." Quality of first customer experience > calendar pressure. Perseus launches once, on the fully validated compound stack, not iteratively from a half-built cloud-only intermediate. |
| 2026-04-07 | **PERMANENT — DO NOT factor commercial licensing into any model selection ever.** Perseus deployment is private enough that commercial license restrictions are irrelevant. All models are in scope regardless of license terms (Gemma custom license, ReDrafter Apple research license, anything else). | Operator: "dont have to worry about using commercially. its all gonna be private enough at first. dont factor that in ever ever ever." This unblocks Gemma 4, ReDrafter, and any other model previously flagged for customer-facing restrictions. License memo task in Plan 42-5-01 is REMOVED. |
| 2026-04-07 | **Multi-vendor model loadout — not Qwen-only.** Use AirLLM to run 70B models locally via disk-streaming layers. Heavy thinking goes to local AirLLM-Llama-70B / Qwen-72B before escalating to cloud Opus. | Operator caught the narrow Qwen focus and asked about AirLLM, Kimi, etc. The original research mentioned a much wider stack. With no license concerns and AirLLM available, the local tier ceiling is 70B+ not 30B. |
| 2026-04-07 | **PERMANENT — if a model is better than Claude AND cheaper, use it. No tribal loyalty to Anthropic.** | Operator: "yes if something is better than claude and cheaper definitely use it." Model selection is purely on merit. Kimi K2.5 added as cloud vision tier (92.3% OCRBench, $0.47/$2 per M, beats Sonnet on OCR for 6x less). DeepSeek V4 stays as cheap tier. MiMo-V2-Pro stays as agentic. Future models follow the same rule — benchmark + cost wins, no defaulting to Claude out of habit. |
| 2026-04-07 | **REFRAME: Phase 42.5 is NOT a separate phase. It's the execution of existing Phases 40-44 with the upgraded model loadout.** | 8 parallel exploration agents found that .planning/phases/40-44 ALREADY have detailed PLAN.md files (refreshed today 2026-04-07). shared/llm_client.py is 880+ lines and already has: AirLLM provider (HeavyLocalProvider), daemon_name passthrough, BATS 4-layer budget, Phase 23 graduated fallback chains (Opus→Sonnet→Haiku→Ollama), DNA injection, sticky latch for prompt caching, death spiral guard. The Phase 40 PLAN already includes Gemma 4 26B as local fallback. The work is filling gaps, not building parallel infrastructure. Phase 42.5 should EXECUTE Phases 40-44 with the upgraded loadout, not duplicate them. |

## Session Continuity

Last session: 2026-04-07
Stopped at: Plan 42-5-01 created
Next action: Review and approve plan 42-5-01, then run /paul:apply .paul/phases/42-5-local-tier-hardening/42-5-01-PLAN.md
Resume file: .paul/phases/42-5-local-tier-hardening/42-5-01-PLAN.md
