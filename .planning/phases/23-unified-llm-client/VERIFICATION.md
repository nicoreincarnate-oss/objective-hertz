# Phase 23: Unified LLM Client -- Verification

## Summary

Merged the split-brain LLM clients into a single factory with graduated model fallback (Opus->Sonnet->Haiku->Ollama). The implementation creates a provider-based architecture behind the `ANATOMY_UNIFIED_LLM` + `UNIFIED_LLM_FACTORY` feature flags, ensuring zero behavior change when flags are off.

## Implementation Status

### New Files Created (10 files, ~1,500 lines)

| File | Lines | Purpose |
|------|-------|---------|
| `shared/llm_unified.py` | 143 | Protocol, types, FallbackChain config, DEFAULT_CHAINS |
| `shared/llm_providers/__init__.py` | 2 | Package init |
| `shared/llm_providers/anthropic_provider.py` | 186 | Claude API provider with artifact cleanup (thinking/tool_use) |
| `shared/llm_providers/ollama_provider.py` | 119 | Ollama provider with TurboQuant KV cache |
| `shared/llm_providers/heavy_local_provider.py` | 78 | AirLLM + oLLM provider |
| `shared/llm_providers/oj_cloud_provider.py` | 98 | OpenJarvis CloudEngine wrapper |
| `shared/llm_factory.py` | 465 | UnifiedLLMFactory + UnifiedEngineAdapter |
| `shared/llm_shadow.py` | 119 | Shadow comparator for 48h parallel run |
| `shared/llm_withholding.py` | 128 | Error withholding buffer (B-13) |
| `scripts/migrations/041-unified-llm.sql` | 41 | Fallback log + shadow comparison tables |

### Modified Files (3 files)

| File | Change |
|------|--------|
| `shared/llm_client.py` | Singleton dispatch via `UNIFIED_LLM_FACTORY` flag, deprecation docstring on LLMClient |
| `shared/llm_engine_adapter.py` | Deprecation notice in module docstring |
| `openjarvis/agents/executor.py` | UnifiedEngineAdapter injection behind feature flag |

### Test Files (3 new, 1 updated)

| File | Tests | What It Validates |
|------|-------|-------------------|
| `tests/test_phase23_types.py` | 20 | ModelTier, FallbackStep, FallbackChain, LLMResponse, DEFAULT_CHAINS, resolve_tier |
| `tests/test_phase23_providers.py` | 17 | Artifact cleanup (thinking/tool_use), model tier mapping, UnifiedEngineAdapter fallback |
| `tests/test_phase23_factory.py` | 15 | WithholdingBuffer classification, recovery, ShadowComparator, factory init |
| `tests/test_phase23_unified_llm.py` (updated) | 13 | Fixed psycopg mock, added ollama config attributes |

## Test Results

```
PYTHONPATH=. python3 -m pytest tests/test_phase23_types.py tests/test_phase23_providers.py tests/test_phase23_factory.py tests/test_phase23_withholding.py -v
71 passed in 0.10s

PYTHONPATH=. python3 -m pytest tests/test_phase23_unified_llm.py -v
13 passed in 0.03s

PYTHONPATH=. python3 -m pytest tests/test_phase23_fallback_chain.py -v
10 passed in 0.04s
```

Total: 94 tests across 6 test files, all passing.

Note: test_phase23_fallback_chain.py fails when run in the same process as test_phase23_unified_llm.py due to pre-existing sys.modules mock pollution between test files. Both pass when run individually.

## Ruff Check Results

All new files pass ruff with zero errors. Pre-existing linting issues in modified files are out of scope.

## Feature Flags

| Flag | Env Var | Default | Effect |
|------|---------|---------|--------|
| Unified LLM | `ANATOMY_UNIFIED_LLM` | `false` | Enables factory-based routing |
| Factory Mode | `UNIFIED_LLM_FACTORY` | `false` | Uses provider-based factory (vs CloudEngine wrapper) |
| Withholding | `UNIFIED_LLM_WITHHOLDING` | `true` | Error withholding in fallback chain |

## Architecture

```
from shared.llm_client import llm  (unchanged for all 30+ consumers)
          |
          v
  _create_llm_client()
          |
    Flag off? -> LLMClient (existing, zero change)
    Flag on?  -> UnifiedLLMFactory.create()
                      |
                 UNIFIED_LLM_FACTORY=true? -> shared.llm_factory.UnifiedLLMFactory
                 USE_OJ_ENGINE=true?       -> _CloudEngineWrapper
                 Otherwise                 -> LLMClient (with graduated fallback chain)
```

Graduated Fallback Chain:
```
genius (Opus) --fail--> smart (Sonnet) --fail--> fast (Haiku) --fail--> local (Ollama)
```

WithholdingBuffer classification:
- Fatal (raise immediately): authentication, invalid_api_key, unauthorized, permission
- Recoverable (withhold, try next): rate_limit, 429, timeout, 500, 502, 503, overloaded

## Commits

| Hash | Message |
|------|---------|
| fbeb1b5 | feat(23-unified-llm): add unified LLM protocol, types, and fallback chain config |
| 1ffdb86 | feat(23-unified-llm): add LLM provider backends (Anthropic, Ollama, HeavyLocal, OJCloud) |
| 85d5b6f | feat(23-unified-llm): add UnifiedLLMFactory, shadow comparator, and withholding buffer |
| d658b92 | feat(23-unified-llm): wire unified factory into singleton and OJ executor |
| 194a4d7 | chore(23-unified-llm): add DB migration for fallback log and shadow comparison tables |
| 7ed54d5 | test(23-unified-llm): comprehensive test suite for unified LLM client |

## Rollback

Set `ANATOMY_UNIFIED_LLM=false` in `.env` and restart. All new files are inert when the flag is off. Migration creates new tables only (no schema changes to existing tables).

## Next Steps

1. Enable `ANATOMY_UNIFIED_LLM=true` + `UNIFIED_LLM_FACTORY=true` in shadow mode
2. Run 48h parallel validation (ShadowComparator monitors error/latency/cost deltas)
3. Cutover decision when gate criteria met (error delta < 1%, latency P95 < 200ms)
4. Remove deprecated code paths after successful cutover
