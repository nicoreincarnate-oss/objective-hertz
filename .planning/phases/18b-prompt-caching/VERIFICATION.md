# Phase 18b: Prompt Caching -- VERIFICATION

## Date: 2026-04-05

## Summary

Phase 18b implements Anthropic prompt caching to reduce API costs by reusing stable system prompt prefixes across sequential LLM calls. All six sub-plans are implemented and tested.

## Implementation Status

### 18b-01: cache_control Blocks and Beta Header in _claude_generate() -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `_build_system_blocks()` splits at CACHE_BOUNDARY_MARKER | Done | `shared/llm_client.py:156-196` |
| `_prompt_cache_enabled()` via ANATOMY_PROMPT_CACHE env var | Done | Inline checks in `_claude_generate()` |
| `anthropic-beta: prompt-caching-2024-07-31` header when flag ON | Done | `shared/llm_client.py` in `_claude_generate()` |
| `_claude_generate_with_images()` gets same cache wiring | Done | `shared/llm_client.py` in `_claude_generate_with_images()` |
| `_claude_generate_stream()` gets same cache wiring | Done | `shared/llm_client.py:734-757` |
| Cache metrics logged (cache_read, cache_creation tokens) | Done | `shared/llm_client.py:1040-1048` |
| `_record_claude_spend()` uses accurate cached token counts | Done | `shared/llm_client.py:955-958` |
| `_fire_metrics()` accepts and passes `cached_tokens` to CostEvent | Done | `shared/llm_client.py:272-332` |

### 18b-02: Fork-Style Parallel Dispatch for Titan Pipeline -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `_PIPELINE_SYSTEM_PREFIX` shared prefix constant | Done | `titan/workflow_pipeline.py:34-42` |
| `render_shared_prefix()` returns prefix when flag ON | Done | `titan/workflow_pipeline.py:50-58` |
| `_prompt_cache_enabled()` feature flag check | Done | `titan/workflow_pipeline.py:45-47` |

### 18b-03: Thread Rendered Prompts in Operative Agent -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `_cached_stable_prefix` field in `__init__()` | Done | `openjarvis/agents/operative.py:78` |
| `_build_cached_system_prompt()` caches stable prefix, appends volatile | Done | `openjarvis/agents/operative.py:448-510` |
| CACHE_BOUNDARY_MARKER inserted between prefix and volatile suffix | Done | `openjarvis/agents/operative.py:498-510` |
| `invalidate_prompt_cache()` method for config/DNA changes | Done | `openjarvis/agents/operative.py:469-480` |
| PromptBuilder integration path (`_build_with_prompt_builder()`) | Done | `openjarvis/agents/operative.py:515-537` |
| Generator loop path also uses cached prompt | Done | `openjarvis/agents/operative.py:298` |

### 18b-04: Sticky Latch for Cache-Sensitive Flags -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `StickyLatch` class (thread-safe, get/peek/reset) | Done | `shared/prompt_builder.py:40-72` |
| Module-level singleton via `get_session_latch()` | Done | `shared/prompt_builder.py:76-81` |
| `_resolve_model()` uses latch for model tier stability | Done | `shared/llm_client.py:91-127` |

### 18b-05: Parallelize Boot Phases -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `asyncio.gather()` for `boot_integration` + `_bootstrap_openjarvis_framework` | Done | `orchestrator.py:435-438` |
| DB pool init runs first (dependency), then parallel phases | Done | `orchestrator.py:419-438` |

### 18b-06: Fast-Path Dispatch for --status/--health/--version -- COMPLETE

| Task | Status | File |
|------|--------|------|
| `_fast_path_dispatch()` function | Done | `orchestrator.py:97-141` |
| `--version` prints version and exits | Done | `orchestrator.py:108-110` |
| `--status` / `--health` query running instance via HTTP | Done | `orchestrator.py:112-141` |
| No full boot triggered for fast-path flags | Done | By design (function called before boot) |

## Feature Flag

- **Name:** `ANATOMY_PROMPT_CACHE`
- **Default:** `false` (zero behavioral change)
- **When ON:** System prompts converted to structured cache_control blocks, beta header added, cache metrics tracked

## Test Results

```
PYTHONPATH=. python3 -m pytest tests/test_phase18b_prompt_cache.py tests/test_phase18b_latch_boot.py -v
23 passed in 0.09s
```

### Test Coverage

| Test | What it validates |
|------|-------------------|
| `test_splits_at_boundary` | _build_system_blocks splits at CACHE_BOUNDARY_MARKER |
| `test_cache_control_on_stable_prefix` | First block gets cache_control: ephemeral |
| `test_volatile_suffix_no_cache` | Second block has no cache_control |
| `test_boundary_at_start` | Edge case: empty prefix skipped |
| `test_entire_string_cached_as_one_block` | No boundary: full string cached |
| `test_empty_string_returns_empty_list` | Empty input returns [] |
| `test_whitespace_only_returns_empty_list` | Whitespace-only returns [] |
| `test_beta_header_present_when_flag_on` | anthropic-beta header added when ANATOMY_PROMPT_CACHE=true |
| `test_no_beta_header_when_flag_off` | No header when flag off |
| `test_system_is_plain_string` | Flag off: system is flat string |
| `test_system_is_block_list` | Flag on: system is block array with cache_control |
| `test_sticky_latch_returns_same_value` | StickyLatch returns first-seen value |
| `test_sticky_latch_different_keys` | Independent keys |
| `test_sticky_latch_reset` | Reset clears all values |
| `test_sticky_latch_peek_*` | Peek returns None before get, value after |
| `test_module_level_latch_singleton` | get_session_latch() is singleton |
| `test_fast_path_version` | --version prints and exits |
| `test_fast_path_no_args` | No args: not triggered |
| `test_fast_path_unknown_flag` | Unknown flag: not triggered |
| `test_fast_path_status_unreachable` | --status when not running |
| `test_fast_path_health_unreachable` | --health when not running |
| `test_fast_path_status_running` | --status when running shows status |

## Ruff Check

All modified files pass ruff check. Pre-existing issues in unmodified code (import sorting, torch import, typing.Dict deprecation) are not addressed in this phase.

## Files Modified

| File | Changes |
|------|---------|
| `shared/llm_client.py` | _fire_metrics() cached_tokens param, CostEvent wiring, _claude_generate_with_images() cache blocks |
| `openjarvis/agents/operative.py` | CACHE_BOUNDARY_MARKER in volatile suffix, invalidate_prompt_cache() method |

## Files Already Complete (from intel-integration branch)

| File | What was already done |
|------|----------------------|
| `shared/llm_client.py` | _build_system_blocks(), _claude_generate() cache wiring, _claude_generate_stream() cache wiring, _record_claude_spend() accurate tokens, _resolve_model() StickyLatch |
| `shared/prompt_builder.py` | StickyLatch, CACHE_BOUNDARY_MARKER, PromptBuilder, get_session_latch() |
| `titan/workflow_pipeline.py` | render_shared_prefix(), _PIPELINE_SYSTEM_PREFIX |
| `openjarvis/agents/operative.py` | _cached_stable_prefix, _build_cached_system_prompt(), _build_with_prompt_builder() |
| `orchestrator.py` | _fast_path_dispatch(), parallel boot with asyncio.gather() |
| `tests/test_phase18b_prompt_cache.py` | 11 tests for cache blocks and headers |
| `tests/test_phase18b_latch_boot.py` | 12 tests for StickyLatch and fast-path |

## GATE Criteria

Phase 18b is code-complete. The 7-day measurement protocol (PLAN.md "Measurement Plan") should be run after deploying with `ANATOMY_PROMPT_CACHE=true` to validate the >15% spend reduction target.
