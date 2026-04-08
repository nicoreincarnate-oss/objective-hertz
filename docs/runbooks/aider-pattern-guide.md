# Aider Architect+Editor Pattern Guide

The Aider pattern splits code-modifying daemon work into three roles. This is
how Ruflo and Clawdbot ship 80%+ local-tier execution while keeping cloud-grade
quality.

## The roles

### Architect
The "smart" model. Claude Opus 4.6 or Sonnet 4.6, or local-heavy AirLLM-Llama-70B
when latency tolerance is high. Architect does the THINKING:
- Reads the failing test / page brief
- Identifies root cause / page structure
- Names files to change / sections to build
- Writes a step-by-step plan
- Specifies what success looks like (test to add / visual diff threshold)

Architect does NOT write code. It writes plans.

### Editor
The "fast" code model. Qwen2.5-Coder-14B local (default for Phase 42.5) or
Sonnet 4.6 for fallback. Editor does the TYPING:
- Reads Architect's plan
- Reads current file state
- Produces a unified diff (Ruflo) or HTML/CSS (Clawdbot)
- Applies the smallest possible change

Editor is constrained by Architect's plan. It can't go off the rails.

### Verifier
Deterministic checks. NOT an LLM. Daemon code:
- Ruflo: `pytest -x` in macOS sandbox, captures pass/fail + output
- Clawdbot: headless browser screenshot + Qwen3-VL-7B visual scoring
- Conway (if used for ledger reconciliation): SQL schema check + balance verify

Verifier returns pass or fail with a critique.

## The loop

```
1. Architect plans
2. Editor implements
3. Verifier validates
4. If pass: done
5. If fail: feed critique to Architect, ask for revision
6. Loop max 3 iterations
7. If still failing: escalate to full-Sonnet or full-Opus single-shot
```

## Why it works

Cost: Opus runs ~5% of calls (architect only). Local Coder runs ~95%. Total
cost is ~10% of full-Opus loops.

Quality: Editor can't make architectural mistakes because Architect already
made the architectural decisions. Editor can only mistype the implementation,
which Verifier catches via pytest / visual diff.

Reliability: 3 iterations × verifier feedback gives Editor multiple shots.
Aider production data shows ~85% first-iteration success on simple bugs and
~70% on cross-file refactors.

## Daemon-specific patterns

### Ruflo
```python
from shared.aider import run_ruflo_aider_loop
from shared.tiers import TierName

result = await run_ruflo_aider_loop(
    failing_test="test_user_age_calculation",
    error_message="expected 25, got 24",
    file_hint="shared/utils.py",
    llm_client=llm_client,
    repo_root=Path("/Users/majovega/Desktop/Projects/objective-hertz"),
    architect_tier=TierName.SMART,   # Sonnet 4.6
    editor_tier=TierName.LOCAL,      # Qwen2.5-Coder-14B local
    sandbox_runner=ruflo_sandbox,
    max_iterations=3,
)

if result.success:
    apply_patch_to_live_tree(result.final_patch)
else:
    escalate_to_human_review(result)
```

### Clawdbot
```python
from shared.aider import run_clawdbot_aider_loop
from shared.tiers import TierName

result = await run_clawdbot_aider_loop(
    lead_profile=lead_dict,
    llm_client=llm_client,
    visual_scorer=qwen_vl_scorer,
    asset_generator=draw_things_client,
    renderer=playwright_pool,
    architect_tier=TierName.SMART,   # Sonnet plans the page
    editor_tier=TierName.LOCAL,      # Qwen2.5-Coder-14B writes HTML
    visual_tier=TierName.VISION,     # Kimi K2.5 scores screenshots
)
```

## When NOT to use the Aider pattern

- Single-line changes (typos, comment edits) — direct LLM call is fine
- Hot-fix paths under deadline pressure — full-Opus single-shot is faster
- Greenfield large modules (no existing test to verify against) — direct
  Sonnet generation is more efficient
- When Architect and Verifier disagree consistently (>30% of the time) on a
  task class — that means the tools or the test infra is broken, not the loop

## Tuning parameters

| Parameter | Default | Tune when |
|---|---|---|
| `max_iterations` | 3 | Raise to 5 for Clawdbot complex pages, lower to 2 for Ruflo simple bugs |
| `architect_tier` | SMART | Use GENIUS for cross-file refactors, LOCAL_HEAVY for fully-private reasoning |
| `editor_tier` | LOCAL (Qwen2.5-Coder-14B) | Use SMART if local Coder is unreliable on a task class |
| `visual_tier` (Clawdbot only) | VISION (Kimi) | Use SMART if Kimi rate-limits |

## Metrics to watch

- Aider iteration count distribution (target: most fixes in 1-2 iterations)
- Architect cost per fix (target: <$0.05 average for Ruflo)
- Editor success rate per iteration (target: 70%+ at iter 1, 95%+ by iter 3)
- Escalation rate (target: <10% of fixes need a full-Sonnet rescue)

These flow into the Hermes War Room canary dashboard.
