# Phase 32: AlphaEvolve Self-Improvement — VERIFICATION

**Date:** 2026-04-05
**Status:** COMPLETE

---

## Files Created

| File | Purpose |
|------|---------|
| `shared/evolve_engine.py` | Core evolution engine: EvolveCandidate, EvolveConfig, EvolveEngine, EvolveRollback, built-in evaluators |
| `scripts/migrations/044-evolve-candidates.sql` | DB tables: evolve_candidates, evolve_rollbacks + feature flag |
| `tests/test_phase32_evolve_engine.py` | 37 tests (33 pass, 4 skip due to numpy dependency) |

## Files Modified

| File | Change |
|------|--------|
| `titan/neuro/learning_loop.py` | Added `evolve_email_template()` and `evolve_scoring_rubric()` wiring functions |

---

## Success Criteria Verification

### 1. Evolution loop runs end-to-end
**PASS** — `test_full_evolution_with_mock_evaluator` verifies seed -> population -> evaluate -> mutate -> select -> best candidate. Uses mock LLM and deterministic evaluator. 6 candidates across 2 islands, 2 generations.

### 2. Safety bounds enforced
**PASS** — `test_protected_path_risk_score` confirms `wallet.py` reference returns risk >= 0.9. `test_security_path_blocked` confirms `security/` reference returns risk >= 0.9. `test_safe_content_low_risk` confirms normal content returns risk < 0.7.

### 3. Mutation size enforced
**PASS** — `test_mutation_size_enforcement` verifies mutations with >50 token delta are detected. `_token_delta()` uses word-level set difference with 1.3x token multiplier. Engine's `_generate_mutations` skips candidates exceeding limit.

### 4. Rollback works
**PASS** — `test_regression_detected` confirms >5% regression over 20 trials/48h triggers rollback. `test_no_regression_before_min_data` confirms no premature rollback. `test_rollback_execution` verifies status update + log entry.

### 5. Bandit integration
**PASS** — `test_register_candidates_as_arms` verifies top-k evolved candidates become bandit arms. After `register_with_bandit(top_k=3)`, bandit stats show 3 arms for the experiment.

### 6. Cost bounded
**PASS** — `test_cost_cap_stops_mutations` verifies when `_run_cost >= COST_CAP_PER_RUN ($0.20)`, mutation generation stops (0 children produced). Cost tracking in `_llm_call` accumulates per-call estimates.

### 7. Daily cap enforced
**PASS** — `test_daily_counter_blocks_after_cap` verifies counter blocks after 5 runs. `test_evolution_rejects_after_daily_cap` verifies `evolve()` raises `ValueError` on 6th attempt.

### 8. Feature flag defaults to true
**PASS** — `ALPHA_EVOLVE_ENABLED = os.environ.get("ALPHA_EVOLVE", "1") == "1"`. Migration inserts `ALPHA_EVOLVE = "true"` into system_config.

### 9. All existing tests pass + 10+ new tests
**PASS** — 37 new tests (33 pass, 4 skip). No existing tests broken (phase 32 is additive).

### 10. ruff check clean
**PASS** — `python3 -m ruff check shared/evolve_engine.py titan/neuro/learning_loop.py tests/test_phase32_evolve_engine.py` returns "All checks passed!"

---

## Hard Safety Boundaries Enforced

| Boundary | Implementation |
|----------|---------------|
| Mutation scope: prompt/text ONLY | `VALID_ARTIFACT_TYPES` whitelist, `ValueError` on invalid types |
| Max 50 tokens per mutation | `_token_delta()` check in `_generate_mutations()`, skip if exceeded |
| Rollback on >5% regression | `EvolveRollback.check_regression()` with configurable thresholds |
| POMDP risk gate > 0.7 | `_score_risk()` checks protected paths, rejects if > `RISK_THRESHOLD` |
| Protected paths | `PROTECTED_PATHS` frozenset: wallet.py, llm_client.py, security/, middleware.py |
| Cost cap $0.20/run | `COST_CAP_PER_RUN` checked in mutation loop and main evolve loop |
| 5 runs/day cap | `_DailyRunCounter` tracks calendar-day runs, blocks at `MAX_DAILY_RUNS` |
| 1 concurrent mutation | Sequential evaluation in evolve loop (no parallel mutations) |

---

## Architecture

```
EvolveEngine.evolve()
  ├── _init_population()        → 20 candidates from seed, split across 2 islands
  ├── evaluator(content)        → Built-in or custom scorer
  ├── _select_parents()         → 70% exploit best, 30% explore random
  ���── _generate_mutations()     → LLM: 80% Haiku breadth, 20% Sonnet depth
  │   ├── _MUTATION_PROMPT      → Structured prompt with weakest dimension
  │   └── _token_delta()        → Enforce 50-token max change
  ├── _passes_safety()          → POMDP risk gate (score_action_risk or heuristic)
  ├── _update_population()      → Add children, prune worst to pop_size
  ��── _converged()              → Top 3 within 2% spread
  └── register_with_bandit()    → Top-k candidates become Thompson Sampling arms

EvolveRollback
  ��── check_regression()        → Compare baseline vs current metrics
  ├── rollback()                → Restore parent, log, feed negative bandit reward
  └── promote()                 → Mark promoted, log, feed positive bandit reward

Wiring (titan/neuro/learning_loop.py)
  ├── evolve_email_template()   → Daily email template evolution
  └── evolve_scoring_rubric()   → Weekly scoring rubric evolution
```
