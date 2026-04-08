# Scratch: daemon llm.generate() call-site survey

Searched 8 daemon directories in `/Users/majovega/Desktop/Projects/objective-hertz/{perseus,titan,hermes,clawdbot,conway,deerflow_research,ruflo,openjarvis}` for patterns: `llm.generate(`, `llm_client.`, `from shared.llm_client`, `from shared.tiers`, `.acomplete(`, `.chat(`.

## perseus (3 files, 5 calls)
- perseus/scout.py:20 — `from shared.llm_client import llm`
- perseus/scout.py:354 — `await llm.generate(...)` (multi-line, lead scout)
- perseus/scout.py:545 — `await llm.generate(prompt, model="fast", max_tokens=300, temperature=0.1)`
- perseus/self_audit.py:20 — `from shared.llm_client import llm`
- perseus/self_audit.py:286 — `await llm.generate(...)` (multi-line)
- perseus/self_audit.py:613 — `await llm.generate(prompt, model="fast", max_tokens=1000, temperature=0.0)`
- perseus/sleep_cycle.py:25 — `from shared.llm_client import llm`
- perseus/sleep_cycle.py:332 — `await llm.generate(prompt, model="genius", temperature=0.3, max_tokens=3000, ...)`
- perseus/sleep_cycle.py:457 — `await llm.generate(prompt, model="genius", temperature=0.4, max_tokens=2500, ...)`

## titan (11 files, 22 calls)
- titan/negotiation.py:86 — `from shared.llm_client import llm`
- titan/negotiation.py:87 — `await llm.generate(...)`
- titan/negotiation.py:139 — `from shared.llm_client import llm`
- titan/negotiation.py:164 — `await llm.generate(...)`
- titan/a2a_server.py:215 — `from shared.llm_client import llm`
- titan/a2a_server.py:238 — `await llm.generate(prompt, model="fast", max_tokens=300)`
- titan/a2a_server.py:251 — `from shared.llm_client import llm`
- titan/a2a_server.py:271 — `await llm.generate(prompt, model="smart", max_tokens=400, temperature=0.1)`
- titan/single_prompt_experiment.py:44 — `from shared.llm_client import llm`
- titan/single_prompt_experiment.py:80 — `await llm.generate(...)`
- titan/memory.py:24 — `from shared.llm_client import llm`
- titan/memory.py:451 — `await llm.generate(prompt, model="smart", temperature=0.4)`
- titan/memory.py:591 — `await llm.generate(prompt, model="smart", temperature=0.3)`
- titan/memory.py:815 — `await llm.generate(prompt, model="fast", temperature=0.2)`
- titan/memory.py:1067 — `await llm.generate(...)` (multi-line)
- titan/memory.py:1216 — `await llm.generate(...)` (multi-line)
- titan/memory.py:1415 — `await llm.generate(prompt, model="fast", temperature=0.2)`
- titan/memory.py:1482 — `await llm.generate(...)` (multi-line)
- titan/memory.py:1567 — `await llm.generate(...)` (multi-line diff)
- titan/memory.py:1648 — `await llm.generate(...)` (multi-line tone)
- titan/expansion.py:27 — `from shared.llm_client import llm`
- titan/expansion.py:244 — `await llm.generate(prompt, model="smart", temperature=0.3, use_dna=True, daemon_name="titan")`
- titan/pipeline/lead_discovery.py:19 — `from shared.llm_client import llm`
- titan/pipeline/lead_discovery.py:344 — `await llm.generate(prompt, model="fast", temperature=0.3)`
- titan/pipeline/lead_discovery.py:421 — `await llm.generate(prompt, model="fast", temperature=0.8)`
- titan/pipeline/close_deal.py:18 — `from shared.llm_client import llm`
- titan/pipeline/close_deal.py:257 — `await llm.generate(prompt, model="smart", temperature=0.6, use_dna=True, daemon_name="titan")`
- titan/pipeline/close_deal.py:281 — `await llm.generate(...)` (attack)
- titan/pipeline/close_deal.py:290 — `await llm.generate(...)` (optimized)
- titan/pipeline/close_deal.py:300 — `await llm.generate(...)` (tone)
- titan/pipeline/email_compose.py:38 — `from shared.llm_client import llm`
- titan/pipeline/email_compose.py:321 — `await llm.generate(prompt, model=model, temperature=0.7, use_dna=True, daemon_name="titan")`
- titan/pipeline/email_compose.py:568 — `await llm.generate(prompt, model=model, temperature=0.8, use_dna=True, daemon_name="titan")`
- titan/pipeline/email_compose.py:860 — `await llm.generate(prompt, model=model, temperature=0.8, use_dna=True, daemon_name="titan")`
- titan/pipeline/rlm_composer.py:25 — `from shared.llm_client import llm`
- titan/pipeline/rlm_composer.py:323 — `await llm.generate(prompt, model=model, temperature=0.8)`
- titan/pipeline/follow_up.py:11 — `from shared.llm_client import llm`
- titan/pipeline/follow_up.py:326 — `await llm.generate(prompt, model="fast", max_tokens=50, temperature=0.3)`
- titan/pipeline/follow_up.py:369 — `await llm.generate(prompt, model="fast", temperature=0.8)`
- titan/pipeline/lead_research.py:20 — `from shared.llm_client import llm`
- titan/pipeline/lead_research.py:326 — `await llm.generate(prompt, model=model, temperature=0.5)`
- titan/neuro/tribe_service.py:98 — `from shared.llm_client import LLMClient`
- titan/neuro/tribe_service.py:107 — `await llm.generate(...)` (multi-line)

## hermes (6 files, 8 calls)
- hermes/daemon.py:143 — `from shared.llm_client import llm`
- hermes/daemon.py:144 — `await llm.generate(...)` (routing)
- hermes/jarvis/step_planner.py:67 — `from shared.llm_client import llm`
- hermes/jarvis/step_planner.py:70 — `await llm.generate(...)`
- hermes/a2a_server.py:397 — `from shared.llm_client import llm`
- hermes/a2a_server.py:405 — `await llm.generate(prompt, model="fast", max_tokens=300)`
- hermes/a2a_server.py:422 — `from shared.llm_client import llm`
- hermes/a2a_server.py:442 — `await llm.generate(prompt, model="smart", max_tokens=400, temperature=0.1)`
- hermes/a2a_server.py:486 — `from shared.llm_client import llm`
- hermes/a2a_server.py:487 — `await llm.generate(prompt, model="smart", max_tokens=600, temperature=0.1)`
- hermes/jarvis/vision_analyzer.py:66,122 — `from shared.llm_client import llm` (vision paths)
- hermes/web/insights.py:9 — `from shared.llm_client import llm`
- hermes/web/insights.py:66 — `await llm.generate(...)` (multi-line)
- hermes/web/app.py:2264 — `from shared.llm_client import llm`
- hermes/web/app.py:2288 — `await llm.generate(prompt, model="local", max_tokens=500, temperature=0.3)`

## clawdbot (9 files, 13 calls)
- clawdbot/page_assembler.py:536 — `from shared.llm_client import llm`
- clawdbot/page_assembler.py:537 — `await llm.generate(...)`
- clawdbot/site_builder.py:32 — `from shared.llm_client import llm`
- clawdbot/site_builder.py:798 — `await llm.generate(...)`
- clawdbot/site_builder.py:816 — `await llm.generate(...)` (retry)
- clawdbot/site_builder.py:1412 — `await llm.generate(...)`
- clawdbot/site_builder.py:1707 — `await llm.generate(...)`
- clawdbot/site_builder.py:1819 — `await llm.generate(...)`
- clawdbot/site_builder.py:1976 — `await llm.generate(...)` (fallback)
- clawdbot/visual_scorer.py:214 — `from shared.llm_client import llm` (vision)
- clawdbot/section_planner.py:488 — `from shared.llm_client import llm`
- clawdbot/section_planner.py:491 — `await llm.generate(...)`
- clawdbot/daemon.py:1436 — `from shared.llm_client import llm`
- clawdbot/daemon.py:1438 — `await llm.generate(...)` (extraction)
- clawdbot/site_quality.py:9 — `from shared.llm_client import llm`
- clawdbot/a2a_server.py:267 — `from shared.llm_client import llm`
- clawdbot/a2a_server.py:276 — **`await llm.generate(prompt, tier="fast", max_tokens=300)` ⚠️ BUG: uses `tier=` kwarg, main's signature is `model=`. This call currently raises TypeError — PORT-PLAN must flag.**
- clawdbot/taste_analyzer.py:615 — `from shared.llm_client import llm`
- clawdbot/brain.py:17 — `from shared.llm_client import llm`
- clawdbot/brain.py:73 — `await llm.generate(...)`
- clawdbot/section_agent.py:500 — `from shared.llm_client import llm`
- clawdbot/section_agent.py:501 — `await llm.generate(...)`

## conway (0 calls — only prose refs)
- conway/survival.py:10,225 — **prose docstrings** referencing `shared/llm_client.py` as an integration target, but no actual `llm.generate()` call. Conway returns tier strings that the caller uses to pick a model. Zero direct LLM calls.

## deerflow_research (2 files, 2 calls)
- deerflow_research/scoring.py:278 — `from shared.llm_client import LLMClient`
- deerflow_research/scoring.py:302 — `await llm.generate(...)` (multi-line)
- deerflow_research/daily_brief.py:143 — `from shared.llm_client import llm`
- deerflow_research/daily_brief.py:167 — `await llm.generate(...)` (multi-line)

## ruflo (1 file, 4 calls)
- ruflo/agent.py:85 — `from shared.llm_client import llm`
- ruflo/agent.py:100 — `await llm.generate(prompt, model="smart", temperature=0.2)`
- ruflo/agent.py:342 — `from shared.llm_client import llm`
- ruflo/agent.py:344 — `await llm.generate(...)` (multi-line)
- ruflo/agent.py:359 — `from shared.llm_client import llm`
- ruflo/agent.py:360 — `await llm.generate(...)` (multi-line)
- ruflo/agent.py:386 — `from shared.llm_client import llm`
- ruflo/agent.py:387 — `await llm.generate(...)` (multi-line)

## openjarvis (5 files, 6 calls)
- openjarvis/engine/nexa_shim.py:102 — `for token in llm.generate(prompt, max_tokens=req.max_tokens):` — **non-Perseus llm var** (Nexa SDK shim, different `llm` object).
- openjarvis/engine/nexa_shim.py:133 — `llm.generate(prompt, max_tokens=req.max_tokens)` — Nexa SDK, NOT shared.llm_client.
- openjarvis/core/hooks.py:180 — `from shared.llm_client import ask_llm` — **stale import**, `ask_llm` does not exist in main's llm_client.py (only `llm` singleton + classes). Another latent bug.
- openjarvis/vassals/sleep_cycle.py:24 — `from shared.llm_client import llm`
- openjarvis/vassals/sleep_cycle.py:175 — `await llm.generate(prompt, model="genius", temperature=0.3, max_tokens=3000, ...)`
- openjarvis/vassals/sleep_cycle.py:220 — `await llm.generate(prompt, model="genius", temperature=0.4, max_tokens=2500, ...)`
- openjarvis/vassals/perseus_scheduler.py:388 — `from shared.llm_client import llm`
- openjarvis/vassals/perseus_scheduler.py:390 — `await llm.generate(...)` (multi-line)
- openjarvis/agents/operative.py:581 — **prose comment** only, no call.

## Summary

| Daemon | Import sites | generate() calls |
|---|---|---|
| perseus | 3 | 5 |
| titan | 11 | 22 |
| hermes | 6 | 8 |
| clawdbot | 9 | 13 |
| conway | 0 | 0 |
| deerflow_research | 2 | 2 |
| ruflo | 1 | 4 |
| openjarvis | 3 (Perseus path) + 2 (Nexa shim, unrelated) | 3 (Perseus) + 2 (Nexa) |
| **TOTAL (Perseus path only)** | **35** | **57** |

### Unique call signatures (deduplicated) — Perseus path

1. `llm.generate(prompt, model="fast", max_tokens=N, temperature=T)` — most common
2. `llm.generate(prompt, model="smart", temperature=T)`
3. `llm.generate(prompt, model="smart", temperature=T, use_dna=True, daemon_name="titan")` — DNA injection (titan only)
4. `llm.generate(prompt, model="genius", temperature=T, max_tokens=N, ...)` — sleep_cycle heavy reasoning
5. `llm.generate(prompt, model="local", max_tokens=500, temperature=0.3)` — hermes web fallback
6. `llm.generate(prompt, model=model, ...)` — dynamic model (email_compose, rlm_composer, lead_research)
7. Multi-line `llm.generate(prompt, system=..., model=..., max_tokens=..., temperature=..., client_id=..., pipeline_stage=...)` — titan.memory, titan.close_deal attacks, hermes insights

### `tier=` param usage (worktree's new naming)

- **ZERO daemons in main repo use a `tier=` parameter** — every call uses `model=` (matching main's `llm_client.py` signature).
- Single exception: `clawdbot/a2a_server.py:276` uses `tier="fast"` — **this is a latent bug** that raises TypeError when called. Must be fixed as part of PORT-PLAN REFACTOR step.
- Worktree's `tiers.py::TierName` is not imported anywhere in any daemon.

### `from shared.tiers` count

- **0 matches** across all 8 daemons. Confirms P0-2 audit finding: worktree's `shared/tiers.py` is an orphan module that no daemon ever imports. Every daemon call in main still passes raw string aliases (`"fast"`, `"smart"`, `"genius"`, `"local"`) to `llm.generate(model=...)`.

### DNA injection sites

- Only `titan` uses `use_dna=True, daemon_name="titan"` (7 call sites across expansion.py, close_deal.py, email_compose.py).
- No other daemon wires DNA — DNA feature flag is titan-exclusive in current call sites.

### Latent bugs discovered during survey (for PORT-PLAN)

1. `clawdbot/a2a_server.py:276` — `tier="fast"` kwarg does not exist in `LLMClient.generate()` signature → TypeError on call.
2. `openjarvis/core/hooks.py:180` — `from shared.llm_client import ask_llm` — `ask_llm` is not defined in main's `shared/llm_client.py` → ImportError on load.

Both are pre-existing and unrelated to Phase 42.5 v2 recovery, but should be mentioned in PORT-PLAN operator questions.
