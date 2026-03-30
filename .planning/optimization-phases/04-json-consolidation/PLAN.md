# Phase 4: JSON Extraction Consolidation (Reduced Scope)

## Goal
Create `shared/json_utils.py` with robust JSON extraction utilities. Migrate only the 10-12 simplest identical call sites. Leave complex sites for future refactor.

## Tasks

### 4.1 Create shared/json_utils.py
New file with two functions:

```python
def extract_json_object(text: str, *, fallback: dict | None = None) -> dict:
    """Extract first JSON object from text containing surrounding content.

    Handles: bare JSON, markdown fences, text before/after JSON.
    On failure: returns fallback if provided, else raises json.JSONDecodeError.
    """

def extract_json_array(text: str, *, fallback: list | None = None) -> list:
    """Extract first JSON array from text containing surrounding content.

    Same semantics as extract_json_object but for [...] arrays.
    """
```

Implementation:
- Strip markdown code fences (```json ... ```) first
- Then fall back to find("{") / rfind("}") pattern (preserving current behavior)
- Error handling matches existing: JSONDecodeError/ValueError caught, fallback returned if provided
- ~50 LOC total

### 4.2 Write comprehensive tests for json_utils
File: `tests/test_json_utils.py`
Test cases:
- Valid JSON object with no surrounding text
- Valid JSON object wrapped in explanation text
- Valid JSON inside markdown code fence
- Malformed JSON → fallback returned
- No JSON present → fallback returned
- Empty string → fallback returned
- Nested JSON objects (outer extraction)
- JSON array extraction
- None fallback → exception raised

### 4.3 Migrate simple identical call sites (10-12 sites)
**Criteria for migration**: Site uses the exact pattern `find("{")` / `rfind("}")` / `json.loads()` with:
- No retry logic after failure
- No regex fallback
- No multi-block extraction
- Returns dict or None on failure

**Candidate files** (verify each before migrating):
- `shared/magma.py:484-486` — simple extract
- `shared/execution_loop.py:92-94` — simple extract
- `shared/weight_directives.py:63-65` — simple extract
- `clawdbot/brain.py:82-84` — simple extract
- `clawdbot/site_quality.py:259-260` — simple extract
- `hermes/web/insights.py:74-75` — simple extract
- `titan/pipeline/lead_research.py:269-271` — simple extract
- `titan/pipeline/follow_up.py:371-372` — simple extract
- `perseus/scout.py:540-543` — simple extract with bounds check
- `ruflo/agent.py:101-102` — simple extract

**For each site**: Replace 3-5 lines with single call:
```python
from shared.json_utils import extract_json_object
parsed = extract_json_object(result, fallback={})
```

### 4.4 Document remaining complex sites
Add a comment block at top of `shared/json_utils.py`:
```python
# KNOWN UNMIGRATED SITES (complex extraction, needs dedicated refactor):
# - titan/memory.py (7 instances, some with retry logic)
# - clawdbot/site_builder.py (3 instances, array + multi-block)
# - titan/pipeline/email_compose.py (2 instances, retry on failure)
# - titan/pipeline/close_deal.py (2 instances, custom fallback dicts)
# - titan/pipeline/lead_discovery.py (2 instances, array extraction)
# - perseus/sleep_cycle.py (2 instances, structured response parsing)
```

## Validation
- [ ] `tests/test_json_utils.py` all pass (9+ test cases)
- [ ] `ruff check` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -x` — full suite passes
- [ ] Each migrated site: manual review confirms error path matches original
- [ ] Each migrated site: compare function output with old implementation for 3 test inputs
- [ ] Complex sites documented but untouched

## LOC Impact
- New: `shared/json_utils.py` (~50 LOC)
- New: `tests/test_json_utils.py` (~80 LOC)
- Modified: 10-12 files (replace 3-5 lines each with 1-2 lines)
- Net: **~-100 LOC** (production code)

## Risk: MEDIUM-LOW
- New utility is well-tested before any migration
- Only simple identical sites migrated — no behavioral edge cases
- Complex sites explicitly left alone
- Each migration is independently reviewable
