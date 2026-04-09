# CODEX_HANDOFF.md — Phase 4: LiteLLM Metadata Migration

> **For Codex CLI on the Studio.** Open this file, then run: `codex "follow CODEX_HANDOFF.md"`

---

## Status

**Phase 3 (Daemon Wiring): COMPLETE** — 86/92 tests pass, 6 pre-existing failures, zero regressions.

**Phase 4 (LiteLLM Metadata Migration): PENDING** — this document is the spec.

---

## What Phase 4 Does

Every `llm.generate()` / `engine.generate()` call in the codebase needs two keyword arguments added:
- `operation="<daemon>.<function_name>"` — traces which code path triggered the LLM call
- `daemon_name="<daemon>"` — routes the call through the correct LiteLLM tier

Without these, tiered routing (LiteLLM proxy) silently falls back to direct Anthropic. The metadata is how LiteLLM knows whether to use `genius`, `smart`, `local`, `codex`, etc.

The `shared/llm_client.py` `LLMClient.generate()` signature already accepts `**kwargs` and passes them through. No signature changes needed — just add the two kwargs at each call site.

---

## Scope

**47 files, ~105 call sites** across all daemons.

The manifest at `/tmp/migrate_to_litellm_manual_review.json` covers 31 openjarvis sites with pre-computed `operation` and `daemon_name` values. The remaining ~74 sites across perseus/, titan/, clawdbot/, hermes/, ruflo/, shared/ need the same treatment.

### File list (all unmigrated call sites)

#### openjarvis/ (31 sites — manifest covers these exactly)
```
openjarvis/system.py:101
openjarvis/sdk.py:310
openjarvis/security/guardrails.py:193
openjarvis/agents/rlm.py:265, 297
openjarvis/agents/executor.py:135
openjarvis/agents/_stubs.py:160
openjarvis/server/routes.py:132
openjarvis/cli/ask.py:476
openjarvis/vassals/perseus_scheduler.py:390
openjarvis/vassals/sleep_cycle.py:175, 220
openjarvis/sessions/compression.py:108
openjarvis/telemetry/wrapper.py:31
openjarvis/telemetry/instrumented_engine.py:93, 99, 104
openjarvis/engine/multi.py:75
openjarvis/learning/optimize/llm_optimizer.py:60, 82, 108, 418, 450
openjarvis/learning/intelligence/grpo_trainer.py:290
openjarvis/learning/intelligence/orchestrator/grpo_trainer.py:392
openjarvis/learning/intelligence/orchestrator/policy_model.py:270
openjarvis/learning/optimize/feedback/judge.py:94
openjarvis/evals/core/scorer.py:44
openjarvis/evals/backends/jarvis_direct.py:68
openjarvis/evals/scorers/_checklist.py:117
openjarvis/evals/execution/webchorearena_env.py:533
```

#### titan/ (~10 sites)
```
titan/memory.py
titan/negotiation.py
titan/neuro/tribe_service.py
titan/single_prompt_experiment.py
```

#### clawdbot/ (~10 sites)
```
clawdbot/brain.py
clawdbot/daemon.py
clawdbot/page_assembler.py
clawdbot/section_agent.py
clawdbot/section_planner.py
clawdbot/site_builder.py
```

#### hermes/ (~4 sites)
```
hermes/daemon.py
hermes/web/insights.py
```

#### perseus/ (~4 sites)
```
perseus/scout.py
perseus/self_audit.py
perseus/sleep_cycle.py
```

#### ruflo/ (~3 sites)
```
ruflo/agent.py
```

#### shared/ (~20 sites)
```
shared/llm_client.py        ← internal generate() impl — MAY NOT need kwargs added; verify
shared/aider/clawdbot_loop.py
shared/aider/ruflo_loop.py
shared/anti_slop.py
shared/consolidator.py
shared/evolve_engine.py
shared/execution_loop.py
shared/imagegen/draw_things_client.py
shared/magma.py
shared/prospect_simulator.py
shared/skill_loader.py
shared/telemetry.py
shared/verifier/consistency.py
shared/voice/intent_router.py
shared/weight_directives.py
```

---

## Migration Rule

For **every** multi-line `.generate(` call that does NOT already have `daemon_name=` and `operation=`:

```python
# BEFORE
result = self.llm.generate(
    messages,
    model=model,
    temperature=0.7,
    max_tokens=2048,
)

# AFTER
result = self.llm.generate(
    messages,
    model=model,
    temperature=0.7,
    max_tokens=2048,
    operation="<daemon>.<function_name>",
    daemon_name="<daemon>",
)
```

### Operation naming convention
`"<daemon>.<function_name>"` — lowercase, dots as separators.

Examples:
- Function `build_site` in clawdbot → `operation="clawdbot.build_site"`
- Function `run_pipeline` in titan → `operation="titan.run_pipeline"`
- Function `_run_alpha` in sleep_cycle (openjarvis vassal) → `operation="sleep_cycle_alpha"`

### Daemon names per directory
| Directory | `daemon_name` |
|-----------|--------------|
| `openjarvis/` | `"openjarvis"` |
| `titan/` | `"titan"` |
| `clawdbot/` | `"clawdbot"` |
| `hermes/` | `"hermes"` |
| `perseus/` | `"perseus"` |
| `ruflo/` | `"ruflo"` |
| `conway/` | `"conway"` |
| `shared/` | use the daemon that owns the calling module — check imports or class name |

---

## What NOT to touch

- `shared/llm_client.py` — the `LLMClient.generate()` implementation itself. It already accepts `**kwargs`. Do not add `daemon_name` to the function signature definition.
- Single-line `.generate(messages)` calls with no keyword args — check if they're the `LLMClient` internal implementation, not a call site.
- Any `model.generate()` from torch/transformers/mlx — these are local model inference, not LiteLLM routing.
- Any call that already has `daemon_name=` — already migrated, skip.

---

## How to verify after migration

```bash
# Zero remaining unmigrated sites (excluding tests and the llm_client.py implementation)
grep -rn "\.generate(" --include="*.py" \
  openjarvis/ titan/ clawdbot/ hermes/ conway/ deerflow/ ruflo/ perseus/ shared/ \
  | grep -v "test_\|_test\.py\|daemon_name\|torch\|tokenizer\|pipeline\|np\.\|uuid\|text_generate" \
  | grep "llm\.\|engine\.\|self\._engine\|self\.engine\|self\.llm\|client\.\|_llm\." \
  | grep -v "shared/llm_client.py"

# Should return empty. Any remaining lines need to be reviewed.
```

```bash
# Run the test suite — must still pass at 86/92
PYTHONPATH=. python3 -m pytest tests/ -x -q 2>&1 | tail -5
```

---

## Commit message to use

```
feat(phase-4): add daemon_name + operation metadata to all llm.generate() call sites

Enables tiered LiteLLM routing — every call now carries the daemon and operation
context required for genius/smart/local/codex tier selection. 105 sites across
47 files in openjarvis/, titan/, clawdbot/, hermes/, perseus/, ruflo/, shared/.
Phase 4 of Phase 42.5 v2 local tier hardening.
```

---

## Context (brief)

**Why this matters:** LiteLLM proxy (`config/litellm_config.yaml`) routes calls to Trinity-Large-Thinking (genius/smart tier, free OpenRouter), Trinity Mini 26B (local tier, Ollama), or direct Anthropic based on `operation` and `daemon_name`. Without the metadata, the proxy falls back to direct Anthropic on every call — no local tier, no cost reduction, no Trinity routing.

**Feature flag:** `LITELLM_PROXY_ENABLED=false` in `.env` — proxy stays off until Phase 42.5 Studio cutover. This migration is safe to do now; the kwargs are silently ignored until the flag flips.

**Phase 3 summary:** 5 waves of daemon wiring are COMPLETE. Metadata plumbing was deferred for Phase 4 because it required libcst or manual review of multi-line call sites (automated single-line migration already ran in Phase 3 Wave 1 — 40 sites done).

---

## Done when

- [ ] All files in the file list above have been visited
- [ ] Zero hits from the verification grep above
- [ ] `pytest` still passes at 86/92 (same pre-existing failures, zero new failures)
- [ ] Commit created with the message above
- [ ] `git push origin claude/charming-elion`
