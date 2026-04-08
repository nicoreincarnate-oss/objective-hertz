# Pre-Launch Audit: /simplify

**Date:** 2026-04-07
**Target:** 40 new Phase 42.5 v2 files from commits `30770c0`, `53dfecc`, `d9fdc6a`
**Skill:** /simplify
**Verdict:** MEDIUM — code is mostly clean for greenfield, but has three specific, high-value simplifications to make before launch. No launch-blockers.

## Scope Correction

The task brief referenced `shared/llm_unified.py`, `shared/llm_factory.py`, and `shared/middleware.py` as the "existing 880-line" infrastructure to reuse. **None of these files exist in the repo.** The actual existing LLM infrastructure is:

- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/llm_client.py` (257 lines) — stringly-typed `LLMClient.generate(model="fast"|"smart"|"genius"|"local"|"local-small")` with its own hardcoded `_COST_PER_1K` cost table and budget gate.
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/execution_loop.py` — pre-existing GSD + Ralph Loop engine, already used by ClawdBot / Titan / Sleep cycle.
- `/Users/majovega/Desktop/Projects/objective-hertz/.claude/worktrees/charming-elion/shared/agent_base.py` — base class for all daemons.

The audit was re-framed around these real files.

## Files Reviewed

### Phase 40-41 backbone (tiering, cost, spend)
- `shared/tiers.py` (494 lines)
- `shared/spend_alerts.py` (189 lines)
- `config/litellm_config.yaml` (319 lines)
- `scripts/show_spend.py` (248 lines)
- `scripts/migrate_to_litellm.py` (349 lines)
- `scripts/rollback_litellm.py` (172 lines)

### Phase 42-43 (caching, classifier, loops)
- `shared/semantic_cache.py` (298 lines)
- `shared/tier_classifier.py` (451 lines)
- `shared/lead_worker.py` (216 lines)
- `scripts/show_cache_stats.py` (60 lines)

### Phase 43 Aider + voice + imagegen + verifier
- `shared/aider/ruflo_loop.py` (383 lines)
- `shared/aider/clawdbot_loop.py` (394 lines)
- `shared/voice/{parakeet,kokoro,intent_router}.py` (393 lines total)
- `shared/imagegen/draw_things_client.py` (171 lines)
- `shared/verifier/{grammar_compiler,consistency,depth_guard,a2a_callback}.py` (620 lines total)
- `shared/escalation_log/redactor.py` (246 lines)

### Spikes + tests
- `scripts/spikes/run_{gbnf,airllm,litellm_hook}_spike.py` (798 lines total)
- `tests/test_phase{40,41,42,43,44}_*.py` + `test_verifier_layers.py` (982 lines total)

---

## Top Findings

### 1. `shared/lead_worker.py` duplicates `shared/execution_loop.py` — NEW abstraction not integrated with existing one

**Severity:** HIGH
**Files:** `shared/lead_worker.py`, `shared/execution_loop.py`, `shared/aider/ruflo_loop.py`, `shared/aider/clawdbot_loop.py`

The pre-existing `shared/execution_loop.py` is a "GSD + Ralph Loop" engine that already implements "break a task into steps, execute, validate, retry with error context, loop until green". It is already used by ClawdBot, Titan, and the Sleep cycle.

The new `shared/lead_worker.py` is a Lead/Worker framework with plan → execute → review → revise semantics. It is functionally the same pattern with different naming (Lead = GSD decomposer, Worker = Ralph executor, review_fn = Ralph validator).

Both the new Aider loops (`ruflo_loop.py`, `clawdbot_loop.py`) were then written as **neither** of these frameworks — they each hand-roll their own architect-editor-verifier loop with `for iteration in range(max_iterations)` and ad-hoc state tracking. So we now have **three** parallel loop abstractions:

1. `shared/execution_loop.py` — Ralph Loop, string-tier models
2. `shared/lead_worker.py` — Lead/Worker, TierName-enum
3. `shared/aider/*_loop.py` — hand-rolled three-role loops

**Dead giveaway that `lead_worker.py` is unused internally:** neither Aider loop imports from it. `ruflo_loop.py` line 40 imports only `from shared.tiers import TierName`. `clawdbot_loop.py` same.

**Recommendation:**
- Pick ONE canonical loop (recommend unifying on `lead_worker.py` because TierName is strongly typed and it's daemon-agnostic with callbacks).
- Refactor `execution_loop.py` to be a thin compatibility shim on top, or migrate callers and delete it.
- Rewrite `ruflo_loop.py` and `clawdbot_loop.py` to use `LeadWorkerLoop` with architect/editor/verifier as `plan_fn` / `execute_fn` / `review_fn`. This deletes ~300 lines of duplicated iteration/state-tracking scaffolding.

### 2. `shared/tiers.py` is a second, parallel model abstraction layer — not integrated with `shared/llm_client.py`

**Severity:** HIGH
**Files:** `shared/tiers.py`, `shared/llm_client.py`

There is no `ModelTier` class in `llm_client.py` — the existing code uses raw strings (`"fast"`, `"smart"`, `"genius"`, `"local"`, `"local-small"`) and has its own cost table at `llm_client.py:22` (`_COST_PER_1K`).

`tiers.py` adds an 11-value `TierName` enum with full fallback chains, cost metadata, SWE-Bench scores, and `TIERS: dict[TierName, TierConfig]`. This is the new source of truth and is richly typed.

**But:** `shared/llm_client.py` has not been touched — it still passes raw strings to `_claude_generate` / `_ollama_generate`. The two layers don't talk. The only connection is alias mapping in `TierName.from_string()` which maps `"sonnet" → "smart"`, `"haiku" → "fast"`, etc.

Result: daemon code in the repo currently accepts `model="fast"` via `llm_client`, but new Phase 42.5 modules (`lead_worker`, `aider/*`, `tier_classifier`) use `TierName.FAST`. These are the same thing expressed in two places. Every new feature now needs a tier-to-string round trip at the boundary (see e.g. `ruflo_loop.py:293` `model=tier.value`).

The `_COST_PER_1K` table in `llm_client.py:22-26` is ALSO duplicated — `TIERS[*].cost_per_m_input/cost_per_m_output` is a richer source of the same data.

**Recommendation:**
- Delete `_COST_PER_1K` in `llm_client.py` and read from `TIERS` instead.
- Refactor `LLMClient.generate()` to accept `TierName | str` and internally canonicalize via `TierName.from_string()`.
- Move `_record_claude_spend` cost calculation to use `TierConfig.cost_per_m_input/output`.
- This is a 20-30 line change that eliminates drift between the old and new cost models and stops future bugs where the two tables diverge.

### 3. Three spike scripts have ~90 lines of copy-pasted main/argparse/stats boilerplate

**Severity:** LOW
**Files:** `scripts/spikes/run_{gbnf,airllm,litellm_hook}_spike.py`

All three spike scripts share the same structure:

```
import argparse, asyncio, json, statistics, sys, time
from dataclasses import dataclass, field
from pathlib import Path

@dataclass class <SpikeName>Result: ...
async def run_spike(...): ...
def format_report(results): ...
def main() -> int: argparse → asyncio.run(...) → print report
```

`run_gbnf_spike.py` is 388 lines, `run_airllm_spike.py` is 253 lines, `run_litellm_hook_spike.py` is 157 lines. Each has its own result dataclass + its own percentile/stats helpers + its own argparse setup.

**Recommendation:** Low priority (these are throwaway spikes), but if they'll be re-run for Phase 42.5 validation, extract `scripts/spikes/_spike_harness.py` with `run_spike_grid()`, `format_percentile_report()`, and a common `SpikeResult` base. Would collapse ~150 lines.

---

## Additional Findings (not in top 3 but worth noting)

### 4. `shared/aider/ruflo_loop.py` and `clawdbot_loop.py` share parser helpers that should be shared

`ruflo_loop.py:339-383` has `_parse_plan()` + `_extract_diff()` (JSON extract from LLM response, markdown fence stripping). `clawdbot_loop.py` has near-identical JSON-from-response parsing for PagePlan and visual verifier critique. Extract to `shared/aider/_parsers.py` — `extract_json_object(response: str)` + `strip_markdown_fences(response: str)`. Would delete ~30 duplicated lines.

### 5. `shared/lead_worker.py` fix_fast flag is a typo-candidate

Line 57: `fail_fast: bool = False` — correct name. Line 160 uses it correctly. No bug, but the nearby `revisable`/`architect_revisits_on_failure` flags overlap conceptually — if `revisable=False` but `architect_revisits_on_failure=True`, what happens? The code checks both (line 162) but the combination is confusing. Collapse to a single `on_failure: Literal["stop", "revise", "continue"] = "revise"` enum.

### 6. `ruflo_loop.py:195` magic number `10000` file truncation

`file_content=file_content[:10000]` — unexplained magic number. Should be a named constant `_EDITOR_FILE_CONTEXT_CHAR_LIMIT = 10_000` with a comment about why (token budget for Qwen2.5-Coder-14B at 4-char/token ≈ 2500 tokens).

### 7. `tests/test_phase43_lead_worker.py` boilerplate is fine

Tests import `from shared.lead_worker import ...` **inside** each test function. This is an anti-pattern — should be at module top. But it's confined to this file and low-severity. No copy-paste across test files detected in spot check.

### 8. `shared/semantic_cache.py` allowlist/forbidden lists will drift

Two `frozenset` lists (`CACHEABLE_OPERATIONS` line 33, `CACHE_FORBIDDEN_OPERATIONS` line 46) with no invariant checking that they're disjoint. If an operator adds `"code_generation"` to `CACHEABLE_OPERATIONS` by mistake, no assertion catches it. Add `assert CACHEABLE_OPERATIONS.isdisjoint(CACHE_FORBIDDEN_OPERATIONS)` at module import.

### 9. `shared/tiers.py` 494 lines with inline documentation is fine

No dead code. The 11-tier registry is exhaustively documented with `use_when` / `avoid_when` lists which is genuinely useful context for the tier_classifier rules. Keep as-is.

### 10. Redactor regex reuse

`shared/escalation_log/redactor.py` (246 lines) appears self-contained. No other file in `shared/` has redact/scrub helpers — confirmed via Grep. No duplication.

---

## Simplifications Proposed (ranked by value)

1. **Unify the three loop abstractions** (`execution_loop.py`, `lead_worker.py`, `aider/*_loop.py`) → delete ~300-500 duplicated lines, single source of truth for "agent loop with verification".
2. **Integrate `tiers.py` into `llm_client.py`** → delete `_COST_PER_1K`, make `LLMClient.generate()` accept `TierName`, single cost source of truth.
3. **Extract Aider JSON/diff parsers** to `shared/aider/_parsers.py` → ~30 lines saved, avoids drift.
4. **Consolidate spike harness** (low priority, post-launch) → ~150 lines saved IF spikes will be reused.
5. **Add disjoint-set assertion** in `semantic_cache.py` → 1 line, prevents footgun.

## Dead Code Flagged For Deletion

**None outright.** Every new file is referenced by at least one test or import in the commits. No obviously-dead functions found. The duplication is the real issue — nothing is useless, it's just written twice.

## What's Actually Good

- `shared/tiers.py` TierName enum + TierConfig dataclass is clean, typed, and well-documented.
- `shared/semantic_cache.py` default-deny allowlist is exactly the right safety posture for code caching.
- `shared/tier_classifier.py` rule-cascade design is clean and testable.
- Test files are mostly small (82-289 lines) with targeted fixtures — no obvious copy-paste disease.
- No files read entire repos or do hot-path blocking work on import.

## Non-Findings (things I checked that were clean)

- No TOCTOU-style pre-existence checks.
- No unbounded data structures or event-listener leaks spotted.
- No N+1 network calls in show_spend.py or show_cache_stats.py.
- No sequential work that should be parallel in the Aider loops (sequential is correct for architect → editor → verifier).
- No raw stringly-typed config in the new modules (good — they use `TierName` + dataclasses).
- No unnecessary JSX nesting (N/A, this is Python).
- Comments are mostly module-level docstrings explaining WHY (architectural decision, cost tradeoffs) — appropriate.

## Recommendation

Land Phase 42.5 v2 with finding #1 (unify loops) and #2 (integrate tiers into llm_client) **before** Phase 44 migration begins, because Phase 44 rewrites every call site — you want the unified abstraction in place first or you'll migrate twice. Findings #3-10 can be post-launch.
