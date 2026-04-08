# Scratch: main repo shared/llm_client.py analysis (1711 lines)

Source: `/Users/majovega/Desktop/Projects/objective-hertz/shared/llm_client.py` @ branch `intel-integration`.

## 1. Public API

- `LLMClient` class (L253-1461) — singleton, deprecated-in-favor-of-factory but still primary.
- `LLMClient.generate(prompt, *, system="", model="auto", max_tokens=2048, temperature=0.7, client_id=None, pipeline_stage="", use_dna=False, daemon_name="") -> str` (L397-539) — **primary entry point**.
- `LLMClient.generate_stream(prompt, *, system, model, max_tokens, temperature, pipeline_stage) -> AsyncGenerator[StreamChunk, None]` (L645-727) — SSE streaming w/ watchdog.
- `LLMClient.generate_with_images(prompt, *, images, system, model, max_tokens, temperature, client_id, pipeline_stage) -> str` (L887-940) — Claude vision, raises on local.
- `LLMClient.classify(text, categories) -> str` (L1435-1447) — uses `local-small` tier.
- `LLMClient.embed(text) -> list[float]` (L1449-1456) — Ollama nomic-embed-text.
- `LLMClient.preconnect()` (L348-366), `LLMClient.close()` (L1458).
- `LLMProtocol` Protocol (L1479-1506) — stable interface.
- `_CloudEngineWrapper` (L1509-1666) — CloudEngine backend w/ same cross-cutting.
- `UnifiedLLMFactory.create()` (L1669-1692) — factory selects backend by env.
- Module singleton: `llm = _create_llm_client()` (L1711).
- `StreamChunk` dataclass (L63-72).
- `_DeathSpiralGuard` class (L199-250) — circuit breaker.

## 2. Tier routing

- Tier aliases resolved in `_resolve_model(tier)` (L91-127) via `_MODEL_MAP` (L99-109):
  - `genius` → `config.claude.genius_model` (Opus)
  - `smart` / `primary` → `config.claude.primary_model` (Sonnet)
  - `fast` / `auto` → `config.claude.fast_model` (Haiku)
  - `local` → `config.ollama.model` (primary Ollama)
  - `local-small` → `config.ollama.secondary` (classification tier)
  - `local-heavy` / `airllm` → `config.airllm.model or config.ollama.model`
  - `embed` → `config.ollama.embed_model`
- Routing fn: `generate()` L430-504 — matches model string and dispatches to one of: heavy-local, best-local, claude, fallback-chain.
- DB-override hook: `get_config(db_key)` w/ StickyLatch (L117-127).
- `generate()` signatures for heavy-local also accept `research-local`, `ollm`, `huge-context-local` (L472).

## 3. Fallback chain

- `_MODEL_FALLBACK_CHAIN` (L77-82): `genius → smart → fast → local`; `smart → fast → local`; `fast → local`.
- Two modes:
  - Legacy binary (L514-539): Claude fails once → `_best_local_generate("local")`.
  - Graduated (L551-643): when `ANATOMY_UNIFIED_LLM=true` — iterates chain, per-tier budget recheck, death-spiral recording, thinking-block stripping when falling back from `genius`.
- Terminal fallback always `_best_local_generate(local)` → Ollama (or AirLLM via routing).
- Exceptions caught broadly inside chain; `last_error` bubbles to log only.
- Streaming fallback (L705-727): TimeoutError or generic Exception → non-streaming generate(), yielded as single chunk.
- Heavy-local backend fallback (L1194-1206): selected backend fails → Ollama.

## 4. StickyLatch cache

- Not defined here; imported via `shared.prompt_builder.get_session_latch` (L40).
- Used in `_resolve_model` (L114-127) — keyed by `f"model_tier:{db_key}"`, one latch per tier per process.
- `latch.peek(key)` returns latched value without creating; `latch.get(key, factory)` latches-or-returns.
- Invalidation: none — lifetime = process (explicitly so dashboard overrides don't bust prompt-cache mid-session).
- `_SESSION_MONTH` (L46) = sibling sticky value — first-day-of-month computed at import, used for budget queries to avoid midnight drift.
- `CACHE_BOUNDARY_MARKER` split in `_build_system_blocks` (L156-196) powers Anthropic prompt caching via `cache_control: ephemeral`.

## 5. Budget gating

- Single authority: `shared.middleware.check_budget_for_llm_call(model)` (imported L87, L497, L912, L1568).
- Called before every Claude path and before every fallback-chain attempt (L498, L587).
- Returns either the same model or a downgraded tier (`local` / `local-small`).
- Downgrade behavior: `generate()` L500-504 — if returned tier is local, routes to `_best_local_generate` immediately.
- Vision path (L913-915) raises instead of downgrading — multimodal can't run on Ollama.
- Spend recording in `_record_claude_spend` (L942-999) — **fail-closed** (IGUS-FIX): DB insert failure raises RuntimeError so caller downgrades to Ollama rather than allowing untracked spend.
- Costs stored in `budget_tracking` table with columns: month, category, amount, description, client_id, pipeline_stage.
- `_COST_PER_1K` (L131-135): haiku=$0.001, sonnet=$0.006, opus=$0.045.
- `_MAX_TOKENS_BY_STAGE` (L140-150): per-stage output caps (classify=50, extract=256, score=128, summarize=512, email_draft=4096, proposal=8192, site_build=8192, research=4096, brief=2048).

## 6. Watchdog / timeout

- Per-tier HTTP request timeout in `_claude_generate` L1028-1029: `genius=180s, fast=60s, default=120s`.
- Streaming watchdog `_STREAM_WATCHDOG_TIMEOUTS` (L55-60): `fast=30s, smart=60s, genius=120s`, default 60s. Measures inter-chunk gap, not total.
- Mechanism: `asyncio.wait_for(aiter.__anext__(), timeout=watchdog_timeout)` in `_aiter_bytes_with_watchdog` (L871-885).
- httpx client: `_get_http()` (L335-346) — connect=30, read=90, write=30, pool=10.
- Preconnect (L348-366): optimistic HEAD to warm TLS.
- `_DeathSpiralGuard` (L199-250): env-tunable max_failures=5, window=300s, cooldown=60s. Trips → all `generate()` calls raise until cooldown.

## 7. AirLLM integration

- Wired: YES, primary heavy-local backend.
- Policy layer: `shared.airllm_policy` (L33-37) — `choose_heavy_local_backend`, `explain_heavy_local_routing`, `should_route_to_heavy_local`.
- Entry points:
  - `_best_local_generate` (L1146-1170) — called from local/local-small paths, routes to heavy backend if policy says so.
  - `_heavy_local_or_ollama_generate` (L1172-1206) — the actual dispatcher; picks between `ollm` and `airllm`.
- AirLLM path: `_airllm_generate` (L1208-1237) uses `asyncio.to_thread` + lazy model load via `_get_airllm_model` (L1239-1263).
- Config: `config.airllm.enabled`, `config.airllm.model`, `config.airllm.compression`, `config.airllm.profiling_mode`, `config.airllm.layer_shards_path`, `config.airllm.hf_token`.
- oLLM path (L1265-1317) — parallel heavy-local backend, uses `ollm` package.
- Fallback: AirLLM unavailable → `_ollama_generate("local")` (L1206).
- Tier aliases that land here: `local-heavy`, `airllm`, `research-local`, `ollm`, `huge-context-local` (L472).

## 8. Cost tracking

- Primary recorder: `_record_claude_spend` (L942-999).
- Schema (DB insert L988-992):
  ```
  INSERT INTO budget_tracking (month, category, amount, description, client_id, pipeline_stage)
  VALUES (%s, 'claude_api', %s, %s, %s, %s)
  ```
- `month` is `_SESSION_MONTH` sticky; `amount` rounded to 4 dp; threshold: only records if cost ≥ $0.001 (L983).
- Accurate-tokens path (L964-972) when `ANATOMY_ACCURATE_TOKENS=true` uses real `usage.input_tokens` / `usage.output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`; else falls back to `len(text)//4` heuristic.
- Fire-and-forget observability via `shared.observability.record_llm_call` and `shared.cost_events.emit_cost_event` in `_fire_metrics` (L272-333) — also emits `CostEvent` w/ cached_tokens for Phase 13 Paperclip Pattern 8.
- CloudEngine wrapper has parallel path (L1600-1619): inserts into `llm_spend` table with schema `(month, model, prompt_tokens, completion_tokens, cost_usd, pipeline_stage)`.
- Image path (L928-936) adds `len(images)*2000` token overhead.

## 9. Line citations quick table

| Feature | Lines |
|---|---|
| Module constants / flags | L46-82 |
| `StreamChunk` dataclass | L63-72 |
| `_MODEL_FALLBACK_CHAIN` | L77-82 |
| `_resolve_model` + StickyLatch | L91-127 |
| `_COST_PER_1K` | L131-135 |
| `_MAX_TOKENS_BY_STAGE` | L140-150 |
| `_build_system_blocks` (prompt cache) | L156-196 |
| `_DeathSpiralGuard` | L199-250 |
| `LLMClient.__init__` | L261-270 |
| `_fire_metrics` | L272-333 |
| `_get_http` / `preconnect` | L335-366 |
| `_inject_dna` | L368-395 |
| `LLMClient.generate` (entry point) | L397-539 |
| `_generate_with_fallback_chain` | L551-643 |
| `generate_stream` | L645-727 |
| `_claude_generate_stream` (SSE) | L729-851 |
| `_sse_lines_with_watchdog` | L853-885 |
| `generate_with_images` | L887-940 |
| `_record_claude_spend` (fail-closed) | L942-999 |
| `_claude_generate` (HTTP call, timeouts) | L1001-1066 |
| `_claude_generate_with_images` | L1068-1131 |
| `_resolve_ollama_model` | L1133-1144 |
| `_best_local_generate` | L1146-1170 |
| `_heavy_local_or_ollama_generate` | L1172-1206 |
| `_airllm_generate` + `_get_airllm_model` | L1208-1263 |
| `_ollm_generate` + `_get_ollm_model` | L1265-1317 |
| `_ollm_generate_sync` | L1319-1352 |
| `_airllm_generate_sync` | L1354-1396 |
| `_ollama_generate` (TurboQuant) | L1398-1433 |
| `classify` / `embed` / `close` | L1435-1460 |
| `LLMProtocol` | L1479-1506 |
| `_CloudEngineWrapper` | L1509-1666 |
| `UnifiedLLMFactory` | L1669-1692 |
| Module singleton `llm` | L1695-1711 |

## Primary entry point exact signature

```python
async def generate(
    self,
    prompt: str,
    *,
    system: str = "",
    model: str = "auto",
    max_tokens: int = _DEFAULT_MAX_TOKENS,  # 2048
    temperature: float = 0.7,
    client_id: int | None = None,
    pipeline_stage: str = "",
    use_dna: bool = False,
    daemon_name: str = "",
) -> str:
```

## Key takeaways for PORT-PLAN

1. Main's `llm_client.py` **already has everything the worktree's new tier modules claim to add**: tier routing, graduated fallback chain, budget gating, StickyLatch cache (via prompt_builder), watchdog timers, AirLLM, cost tracking, death-spiral guard, DNA injection, prompt caching, streaming w/ watchdog, vision.
2. Missing from main: semantic cache (only has per-tier latch), Kokoro/Parakeet voice I/O, image generation, aider integration, escalation log, verifier/grammar compiler, spend-alert daemon, lead-worker pool, Phase 42.5 v2 tier taxonomy (Qwen3-30B-A3B/Coder-14B/8B/VL).
3. Refactor debt: worktree's modules almost certainly **duplicate** main's generate/fallback/budget surfaces under different names; need to rewire to call `from shared.llm_client import llm` instead.
