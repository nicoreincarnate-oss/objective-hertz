# Inference Matrix

## Ideas To Steal From ClawWork

Steal these:
- **real cost accounting** from provider responses, not rough estimates
- **economic pressure / solvency tracking** so agents pay for bad work
- **live worker theater** with visible task, cost, outcome, and artifacts
- **benchmark harness by job type**, not only generic evals
- **work vs learn loop** so background agents decide when to earn vs improve

Do not steal:
- the whole runtime as a replacement for Hermes/OpenJarvis
- the benchmark-centric product shell as our main operator interface

## Model Decisions

### Kirito / Hermes
- `live_voice` -> `smart`
- `memory_query` -> `local-heavy`

### ClawdBot
- `browser_reasoning` -> `smart`
- `web_research_digest` -> `local-heavy`

### Pipeline
- `lead_discovery` -> `fast`
- `lead_research` -> `smart`
- `email_compose` -> `smart`

### Titan
- `memory_consolidation` -> `local-heavy`
- `negotiation` -> `smart`

### OpenJarvis
- `background_research` -> `local-heavy`
- `orchestration` -> `genius`

## AirLLM vs oLLM

### AirLLM
- current heavy-local default for async research/memory
- good first fit because it is already integrated

### oLLM
- second heavy-local backend for ultra-long local context windows
- best current use:
  - giant logs
  - very large document passes
  - compliance / contract analysis
  - huge offline synth jobs

## Code Reality

Heavy-local routing is now active in:
- [perseus/scout.py](/Users/majovega/Desktop/Projects/objective-hertz/perseus/scout.py)
- [shared/magma.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/magma.py)
- [titan/memory.py](/Users/majovega/Desktop/Projects/objective-hertz/titan/memory.py)

Policy is centralized in:
- [shared/inference_policy.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/inference_policy.py)
