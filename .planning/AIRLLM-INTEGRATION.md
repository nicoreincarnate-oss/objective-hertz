# AirLLM Integration

## Smart Placement

AirLLM belongs in the **heavy local reasoning lane**, not Kirito's realtime
voice loop.

Use AirLLM for:
- paper digestion and research batches
- overnight scout summaries
- long-context memory consolidation
- large local analysis when Claude is unavailable or when we explicitly want a
  heavyweight local run

Do not use AirLLM for:
- low-latency buddy conversation
- quick classifications
- embeddings
- short operational prompts where Ollama is already fast enough

## Current Wiring

AirLLM is now integrated behind:
- [shared/config.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/config.py)
- [shared/airllm_policy.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/airllm_policy.py)
- [shared/llm_client.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/llm_client.py)

Behavior:
- explicit `model="local-heavy"` or `model="airllm"` routes to AirLLM first
- heavy local stages like `research`, `intel`, `scout`, `paper`, `memory`,
  `sleep_cycle`, `digest`, and `summary` prefer AirLLM when enabled
- if AirLLM is unavailable, the system falls back cleanly to Ollama

## Activation

Set these in `.env`:
- `AIRLLM_ENABLED=true`
- `AIRLLM_MODEL=<hugging-face-model>`
- optional: `AIRLLM_HF_TOKEN=...`
- optional: tune `AIRLLM_PROMPT_CHAR_THRESHOLD`

## Intended Operator Effect

Kirito/Hermes stays fast for live commands.

When a task becomes:
- long-context
- research-heavy
- memory-heavy
- or explicitly "run the big local model"

the local execution lane can step up to AirLLM without changing the user-facing
surface.
