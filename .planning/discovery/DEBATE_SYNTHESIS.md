# Alpha-Beta Debate Synthesis

**Date:** 2026-03-29
**Task:** Optimal integration approach for 7 intel references into Objective Hertz

## Winner: Alpha's proposal with Beta's corrections

Alpha's 7-phase sequential approach was fundamentally sound. Beta found 3 critical, 5 major, and 4 minor issues. All corrections have been incorporated.

## Critical Corrections Applied

1. **Async migration is its own phase (0b)** — Not bundled with P0 bug fixes. Requires full call-site audit, sync wrapper for backward compat, and per-daemon migration.

2. **RLM budget model fixed** — Per-email cap raised to $0.08. Haiku for evaluation (not Sonnet). Global monthly RLM spend cap of $100 with automatic fallback to single-pass.

3. **PQC ARM64 validation spike** — 2-day spike before Phase 6. Fallback to pqcrypto if liboqs-python fails on M4.

## Major Corrections Applied

4. **Interface contracts in Phase 0** — `shared/contracts.py` with Protocol types for DNAProvider, SlopScorer, MemoryStore, Middleware. Prevents Phase 4 integration cliff.

5. **DNA injection opt-in** — `use_dna=True` parameter, 500-token budget, injection scanner, circuit breaker on elevated error rates.

6. **Memory GC** — Working memory in-process only. Episodic: 30-day expiry + daily cleanup. Semantic: per-daemon row cap (10K) with MAGMA compression.

7. **Anti-Slop best-of-N** — Track scores across iterations, return highest-scoring version. "Good enough" threshold skips unnecessary iterations.

8. **HyperAgents legal safety** — Implement from Thompson sampling prior art (Sutton & Barto 1933/2018). Zero reference to HyperAgents code. Paper cited as inspiration only in intel/.

## Minor Corrections Applied

9. Signed manifests (not just SHA-256) for skill loader
10. Feature flags for ALL phases
11. 2 weeks buffer (after Phase 4 and Phase 7)
12. Observability baseline before adding new subsystems
13. Warm-start bandit with priors from current hardcoded thresholds

## Beta Suggestion Considered but Deferred

- **Cut PQC entirely from v1** — Beta suggested deferring PQC since Conway has no live wallet funds. Kept as Phase 6 because: (a) the operator explicitly included it, (b) ARM64 spike validates feasibility early, (c) feature flag means zero risk if it doesn't work.

## Corrected Phase Order

| Phase | Duration | Key Deliverable | Feature Flag |
|-------|----------|-----------------|-------------|
| 0a | 1 week | Conway P0 fixes, signed skill manifests | N/A |
| 0b | 1 week | Async WorkflowEngine, interface contracts, observability baseline | N/A |
| 1 | 1.5 weeks | Agent DNA profiles with opt-in injection + circuit breaker | ENABLE_DNA_PROFILES |
| 2 | 1.5 weeks | Anti-Slop quality scorer + best-of-N rewrite | ENABLE_ANTI_SLOP |
| 3 | 2 weeks | DeerFlow 3-tier memory with GC + JSON cache | ENABLE_DEERFLOW_MEMORY |
| Buffer | 1 week | Integration checkpoint, fix cross-phase issues | — |
| 4 | 2 weeks | Async middleware chain wiring DNA + Anti-Slop + Memory | ENABLE_MIDDLEWARE |
| 5 | 2 weeks | RLM recursive compose with Haiku eval + spend caps | RLM_ENABLED |
| 6 | 1.5 weeks | PQC hybrid encryption for Conway (ARM64 spike first) | PQC_ENABLED |
| 7 | 2 weeks | Thompson sampling adaptive thresholds for expansion | ENABLE_BANDIT_EXPANSION |
| Buffer | 1 week | Final stabilization + integration testing | — |
| **Total** | **~16 weeks** | | |

## Cost Model (Corrected)

| Phase | Monthly Cost Delta | At 1500 emails/month |
|-------|-------------------|---------------------|
| DNA | +$5-10 | 500 tokens/call overhead |
| Anti-Slop | +$15-30 | Haiku scoring all outbound |
| Memory | +$10-20 | Vector queries + Postgres |
| Middleware | +$5 | Telemetry overhead |
| RLM | +$45-75 | Haiku eval + Sonnet drafts, capped at $100/month |
| PQC | $0 | Computational only |
| Adaptive | +$5-10 | Lightweight bandit updates |
| **Total** | **$85-170/month** | Within $800 budget |

## Confidence: 0.78 (up from Alpha's 0.72)

Confidence increase from: interface contracts reducing integration risk, ARM64 validation spike, budget model realism, feature flags enabling rollback.

## What Beta Caught That Alpha Missed (Learning Signal)
- Budget math at scale (per-email caps vs monthly aggregates)
- Integration cliff at Phase 4 (components built in isolation then wired together)
- Memory table unbounded growth (no GC)
- Anti-Slop rewrite loop could make content worse (no best-of-N tracking)
- HyperAgents clean-room claim legally insufficient (use textbook prior art instead)
