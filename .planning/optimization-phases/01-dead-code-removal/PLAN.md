# Phase 1: Dead Code Removal + Lazy Import Fix

## Goal
Remove confirmed dead code files and fix lazy imports in orchestrator hot loops. Largest LOC reduction with lowest risk.

## Tasks

### 1.1 Delete temporary root scripts
- Delete `tmp_netlify_probe.py` (15 LOC)
- Delete `tmp_check_client_sites.py` (61 LOC)
- Delete `tmp_check_tables.py` (23 LOC)
- Delete `tmp_inspect_sites.py` (35 LOC)
- **Pre-check**: `git grep tmp_netlify_probe tmp_check_client tmp_check_tables tmp_inspect_sites` — confirm zero imports in tests/conftest

### 1.2 Delete firecrawl backup file
- Delete `tools/firecrawl/apps/python-sdk/firecrawl/firecrawl.backup.py` (4,635 LOC)
- **Pre-check**: Verify firecrawl is NOT a git submodule: `git submodule status`
- **Pre-check**: `git grep firecrawl.backup` — confirm zero references
- Leave JS backup (`index.backup.ts`) untouched if firecrawl is vendored/submodule

### 1.3 Move lazy imports to module level in orchestrator.py
- Identify imports inside async def methods that are called every loop cycle
- Candidates: `from shared.pipeline import assess_pipeline_state`, `from tools.budget_guard import get_budget_report`
- Move to module-level imports at top of file
- Only move imports that are called every cycle (not conditional/rare paths like sleep_cycle)
- **Rule**: If the import is inside a `try:` block for optional dependency, leave it lazy

### 1.4 Relocate 8 test-only shared modules
- Move to `tests/helpers/` (create directory):
  - `shared/deep_thinking.py`
  - `shared/dynamic_routing.py`
  - `shared/scientific_loop.py`
  - `shared/hybrid_rag.py`
  - `shared/email_enrichment.py`
  - `shared/milestone_rewards.py`
  - `shared/skill_distiller.py`
  - `shared/debate_qd.py`
- Update import paths in corresponding test files (test_week2, test_week5, test_week7, test_week9, test_week10)
- **Pre-check**: grep each module name across entire codebase to triple-confirm zero production imports

## Validation
- [ ] `git grep` confirms zero references to deleted files
- [ ] `ruff check --select F401,E` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -x` — full suite passes
- [ ] No `__init__.py` modifications required
- [ ] `git diff --stat` shows only deletions + import moves

## LOC Impact
- Deleted: ~5,940 LOC (4 tmp + backup + 8 test-only modules)
- Modified: ~30 LOC (orchestrator imports + test import paths)
- Net: **-5,910 LOC**

## Risk: LOW
- All deleted files have zero production importers (verified by 3 independent checks)
- Lazy import fix is a pure reordering with no semantic change
- Test module relocation only changes import paths, not logic
