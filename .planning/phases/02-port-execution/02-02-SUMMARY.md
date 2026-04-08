---
phase: 02
plan: 02
title: Tier Core Merge
type: summary
wave: 2
depends_on: [02-01]
status: complete
verdict: SHIP
commits:
  - d451776  # Task 1: tier merge + auto→smart
  - fe73697  # Task 2: auto_tier hook + classifier filter fix
  - 0667147  # Task 3: lead_worker verification (empty marker)
metrics:
  tasks_complete: 3
  tasks_total: 3
  tests_run: 41
  tests_passing: 41
  tier_count: 13
  p1_7_deepcopy_count: 5
---

# Phase 2 Plan 02: Tier Core Merge — Summary

**Wave 2 of Phase 2.** Merged worktree's `TierName` enum and main's `ModelTier`
into a single canonical class. Enforced PORT-PLAN decision #1 (`auto → smart`).
Added opt-in `auto_tier` hook in `LLMClient.generate`. Verified `lead_worker.py`
post-merge with P1-7 deepcopy fix preserved.

## One-liner

`shared.tiers.TierName` is now THE canonical tier enum (13 entries: 11
generative + LOCAL_SMALL + EMBED). `shared.llm_unified.ModelTier` re-exports it
verbatim, so legacy imports work unchanged. Operator decision #1 enforced
end-to-end: `auto` resolves to SMART (Sonnet 4.6) everywhere, never FAST.

## Tasks

### Task 1 — REFACTOR `shared/tiers.py` → merge into main `ModelTier`

**Status:** complete
**Commit:** `d451776`
**Effort estimate:** 4h | **Actual:** ~25min

**Pre-state (post-rebase):**
- `shared/tiers.py` (worktree): 11 tiers in `TierName(str, Enum)`,
  full `TierConfig` registry, `from_string` with `auto → smart`.
- `shared/llm_unified.py` (main): 7 tiers in `ModelTier(str, Enum)`
  (genius/smart/fast/local/local-small/local-heavy/embed). Missing CODEX,
  AGENTIC, LONGCTX, CHAT, CHEAP, VISION. `_TIER_ALIASES["auto"] = FAST`
  (the WRONG default per decision #1).
- The two enums shared string values for some tiers but were CLASS-DISTINCT,
  so `TierName.GENIUS is ModelTier.GENIUS` was False. Wave 1's note
  ("`shared.tiers.TierName` already exists in main") was overstated — both
  files existed but the enums were divergent.

**Action taken:**
1. Added `LOCAL_SMALL` and `EMBED` to `TierName` (now 13 values total) with
   full `TierConfig` entries (model id, latency, fallback chain, use_when,
   avoid_when, is_local).
2. Added `LOCAL_SMALL` and `EMBED` to `downgrade_tier` and `upgrade_tier` maps.
3. Replaced `class ModelTier(str, Enum)` in `shared/llm_unified.py` with
   `from shared.tiers import TierName as ModelTier` — they are now literally
   the same Python class (`TierName is ModelTier == True`).
4. Rewrote `_TIER_ALIASES` in `llm_unified.py` to mirror `TierName.from_string`
   and enforce `auto / primary / default / claude / sonnet → SMART`.
5. Changed `resolve_tier()` unknown-fallback from `FAST` → `SMART` (decision
   #1: quality > cost).
6. Patched 3 sites of `if model == "auto": model = "fast"` in
   `shared/llm_client.py` → `model = "smart"` (lines 430, 685, 1549).
7. Updated `tests/test_phase23_types.py`:
   - `len(ModelTier) == 7` → `== 13`
   - `resolve_tier("auto") == FAST` → `== SMART`
   - `test_unknown_defaults_to_fast` → `test_unknown_defaults_to_smart`

**Files modified:**
- `shared/tiers.py` (+88 / -2)
- `shared/llm_unified.py` (+25 / -22)
- `shared/llm_client.py` (3 sites, +9 / -3 net)
- `tests/test_phase23_types.py` (+9 / -4)

**Verification:**
```
TierName count: 13
ModelTier count: 13
TierName is ModelTier: True
TIERS count: 13
Has CODEX/VISION/LOCAL_SMALL/EMBED: True
resolve_tier(auto): TierName.SMART
resolve_tier(unknown): TierName.SMART
TierName.from_string(auto): TierName.SMART
```

**Tests:** 28/28 passing (`test_phase23_types.py` + `test_phase41_tiers.py`).

---

### Task 2 — REFACTOR `shared/tier_classifier.py` → rewire imports + auto_tier hook

**Status:** complete
**Commit:** `fe73697`
**Effort estimate:** 3h | **Actual:** ~20min

**Pre-state:**
- `shared/tier_classifier.py:25` imports `from shared.tiers import TierName`
  — works post-Task 1 since TierName now has all 13 values referenced by
  RULES (VISION, GENIUS, LOCAL_HEAVY, LONGCTX, CODEX, LOCAL, SMART, CHAT,
  CHEAP, FAST). All 14 rules verified to reference valid tiers.
- `shared/llm_client.py:LLMClient.generate` had no `auto_tier` parameter.

**Action taken:**
1. Added `auto_tier: bool = False` kwarg to `LLMClient.generate` signature.
2. Added a hook BEFORE the `auto → smart` fallback: when `auto_tier=True` AND
   `model == "auto"`, instantiate `RuleBasedClassifier()` and call
   `await classify(prompt, operation=pipeline_stage, daemon=daemon_name,
   max_output_tokens=max_tokens)`. Use `result.tier.value` as the resolved
   model. Wrapped in try/except so a classifier failure NEVER blocks the call
   (falls back to `smart`).
3. **[Rule 1 - Bug discovered during verification]** Pre-existing bug in
   `RuleBasedClassifier._matches`: rules with both `operation_matches` AND
   `keyword_patterns` would silently fail when called with empty operation,
   even if a keyword matched. The test
   `test_architecture_keyword_routes_to_genius` (prompt: "Design the
   architecture for a multi-tenant SaaS", operation="") was failing for
   exactly this reason — it routed to SMART (default) instead of GENIUS.
   Fixed by gating the operation/daemon filters on the input being non-empty:
   `if rule.operation_matches and operation:` instead of
   `if rule.operation_matches:`. Filters that don't have input are skipped,
   not failed.

**Files modified:**
- `shared/llm_client.py` (+24 / -2)
- `shared/tier_classifier.py` (+8 / -2)

**Verification:**
```
classify('write a fast email', operation='email_compose', daemon='titan')
  → TierName.SMART (default rule, conf=0.7)
classify('design the system architecture', operation='architecture_decision', daemon='openjarvis')
  → TierName.GENIUS (conf=0.95)
LLMClient.generate signature: auto_tier: bool = False ✓
grep -c 'auto_tier' shared/llm_client.py → 6 (≥ 2)
```

**Tests:** 37/37 passing
(`test_phase23_types.py` + `test_phase41_tiers.py` + `test_phase43_classifier.py`).
The architecture-keyword classifier test now passes — was previously failing
due to the operation-filter bug.

---

### Task 3 — REFACTOR `shared/lead_worker.py` → verify post-merge

**Status:** complete
**Commit:** `0667147` (empty marker)
**Effort estimate:** 2h | **Actual:** ~5min

**Pre-state:**
- `shared/lead_worker.py:26` imports `from shared.tiers import TierName`.
- 5 `copy.deepcopy(context)` sites in `LeadWorkerLoop` (P1-7 fix from Phase 1).
- No tier-name changes needed since TierName now has all values.

**Action taken:** None — verification only.

**Verification:**
```
LeadWorkerConfig(lead_tier=TierName.SMART, worker_tier=TierName.FAST)
  → constructs cleanly
inspect.getsource(LeadWorkerLoop).count('copy.deepcopy') → 5 (P1-7 preserved)
```

**Tests:** 4/4 passing (`test_phase43_lead_worker.py`).

Empty marker commit created (`--allow-empty`) so the plan has a record per task.

---

## Plan-level verification

All 5 verification commands from PLAN.md pass:

| # | Command                                                              | Result |
| - | -------------------------------------------------------------------- | ------ |
| 1 | `TierName is ModelTier`                                              | True   |
| 2 | `len(TIERS) >= 11`                                                   | 13     |
| 3 | `RuleBasedClassifier().classify('test').tier`                        | SMART  |
| 4 | `'copy.deepcopy' in inspect.getsource(LeadWorkerLoop)`               | True   |
| 5 | `pytest test_phase41_tiers.py test_phase43_lead_worker.py + others`  | 41/41  |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Tier classifier operation/daemon filter blocked keyword-only matches**
- **Found during:** Task 2 verification (running `test_phase43_classifier.py`)
- **Issue:** `RuleBasedClassifier._matches` treated empty `operation`/`daemon`
  inputs as filter failures. A rule with both `operation_matches` and
  `keyword_patterns` could never match a keyword-only prompt — the operation
  filter would early-return False before keywords were even checked. The
  `test_architecture_keyword_routes_to_genius` test was failing for this exact
  reason; the bug was masked because no production code was hitting the
  classifier yet.
- **Fix:** Gate operation/daemon filter checks on the input being non-empty
  (`if rule.operation_matches and operation:`). Filters skip when their input
  is unavailable instead of blocking the rule.
- **Files modified:** `shared/tier_classifier.py`
- **Commit:** `fe73697` (folded into Task 2)

### Architectural changes

None.

### Pre-existing assumption corrected

Wave 1's note "`shared.tiers.TierName` already exists in main; verifier
imports work unchanged" was technically true for the import path, but the
two enums (`TierName` and `ModelTier`) were class-distinct with divergent
value sets. The merge work was real, not a no-op. Plan estimate was 9h; actual
~50min thanks to the rebase already aligning the imports.

## Tier count post-merge

13 tiers total in unified `TierName`/`ModelTier`:

| Tier            | Value          | Source                      |
| --------------- | -------------- | --------------------------- |
| GENIUS          | `genius`       | both (worktree wins meta)   |
| SMART           | `smart`        | both (worktree wins meta)   |
| CODEX           | `codex`        | worktree                    |
| AGENTIC         | `agentic`      | worktree                    |
| LONGCTX         | `longctx`      | worktree                    |
| CHAT            | `chat`         | worktree                    |
| FAST            | `fast`         | both (worktree wins meta)   |
| CHEAP           | `cheap`        | worktree                    |
| LOCAL           | `local`        | both (worktree wins meta)   |
| LOCAL_SMALL     | `local-small`  | main → added to worktree    |
| LOCAL_HEAVY     | `local-heavy`  | both (worktree wins meta)   |
| VISION          | `vision`       | worktree                    |
| EMBED           | `embed`        | main → added to worktree    |

## P1-7 Preservation

`shared/lead_worker.py:LeadWorkerLoop` retains all 5 `copy.deepcopy(context)`
sites:
1. Initial plan call (`plan_fn(goal, copy.deepcopy(context), [])`)
2. Per-step execute call (`execute_fn(step, copy.deepcopy(context))`)
3. Failure-revision plan call
4. Periodic review call (`review_fn(plan, results, copy.deepcopy(context))`)
5. Periodic re-plan call

## auto→smart alias enforcement (PORT-PLAN decision #1)

End-to-end enforcement verified:
- `shared.tiers.TierName.from_string('auto')` → `SMART`
- `shared.llm_unified.resolve_tier('auto')` → `SMART`
- `shared.llm_unified._TIER_ALIASES['auto']` → `SMART`
- `shared.llm_client.LLMClient.generate(model='auto')` → resolves to `'smart'`
  (3 sites, lines 430, 685, 1549)
- `resolve_tier('nonexistent')` → `SMART` (was `FAST` previously)

## Self-Check: PASSED

- Files modified all exist and contain expected changes:
  - `shared/tiers.py` — TierName has 13 entries, TIERS has 13 entries
  - `shared/llm_unified.py` — `ModelTier` is `TierName` (re-exported)
  - `shared/llm_client.py` — `auto_tier` kwarg present (6 occurrences)
  - `shared/tier_classifier.py` — operation/daemon filter gated on input
  - `tests/test_phase23_types.py` — count assertion is 13, alias is SMART
- Commits exist:
  - `d451776` — Task 1
  - `fe73697` — Task 2
  - `0667147` — Task 3 (empty marker)
- 41/41 relevant tests passing
