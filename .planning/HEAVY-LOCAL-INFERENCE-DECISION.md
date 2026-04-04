# Heavy Local Inference Decision

## Decision

Perseus should use three distinct inference lanes:

1. **Claude**
   - default for live high-quality operator work
   - Kirito/Hermes voice, planning, high-stakes reasoning

2. **Ollama**
   - default lightweight local lane
   - quick classifications, fallback chat, embeddings, small/fast local tasks

3. **AirLLM**
   - current heavyweight local lane
   - offline/async research, memory, scout, and long-context analysis

AirLLM should **not** replace Claude globally.  
AirLLM should **not** replace Ollama for quick local tasks.  
AirLLM **should** replace Ollama as the default local engine for heavyweight
async reasoning.

## Exact AirLLM Use Cases

Use AirLLM when the task is:
- async or background
- long-context
- memory-heavy
- research-heavy
- log/document/history digestion
- overnight/offline analysis

Examples:
- scout finding evaluation
- paper digestion
- MAGMA memory query decomposition
- causal memory inference
- large local summaries of recent activity

Do not use AirLLM for:
- Kirito voice turns
- quick routing/classification
- short War Room questions
- embeddings
- browser execution loops

## Donor Comparison

### AirLLM
- Best for low-memory huge-model inference and async local reasoning
- Good current fit because it is already wired into Perseus

### oLLM
- Better donor candidate than AirLLM for **very large context offline workloads**
- Strong fit for:
  - 50k to 100k context analysis
  - giant logs
  - contracts / long reports
  - big offline local document passes
- Better future candidate if Perseus needs very large single-pass local context

### ClawWork
- Not a runtime replacement for Perseus
- Worth studying for:
  - swarm workflow patterns
  - terminal-native worker orchestration
  - task decomposition ideas
- Do not replace Hermes/OpenJarvis/War Room with it

## Current Implementation

The AirLLM lane is wired in:
- [shared/config.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/config.py)
- [shared/airllm_policy.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/airllm_policy.py)
- [shared/llm_client.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/llm_client.py)

And now actively preferred in:
- [perseus/scout.py](/Users/majovega/Desktop/Projects/objective-hertz/perseus/scout.py)
- [shared/magma.py](/Users/majovega/Desktop/Projects/objective-hertz/shared/magma.py)

## Next Donor Priority

If we add another heavy-local engine after AirLLM, the best next target is
**oLLM**, not ClawWork.
