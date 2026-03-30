# Phase 2: LLM Cost Estimation Fix + Slow Query Logging

## Goal
Fix budget tracking accuracy by using real API token counts instead of character-based estimates. Add slow query observability to the database layer.

## Tasks

### 2.1 Thread API usage data through cost recording
- **File**: `shared/llm_client.py`
- Current: `_record_claude_spend()` estimates tokens as `len(text) / 4`
- Fix: After `_claude_generate()` and `_claude_generate_with_images()` return, `self._last_usage` already has real token counts from API response
- Modify `_record_claude_spend()` signature to accept optional `usage: dict | None`
- When `usage` provided with `input_tokens` and `output_tokens`: use real values
- When `usage` is None (Ollama path, errors): fall back to existing 4-char estimate
- Update both call sites (`_claude_generate` and `_claude_generate_with_images`) to pass `self._last_usage`
- **Do NOT change**: Ollama cost tracking path, budget enforcement logic, tier routing

### 2.2 Add slow query warning logging
- **File**: `shared/db.py`
- Current: `observe_db_query(operation, duration, success)` already called after every query
- Add: `SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_MS", "500"))`
- After each `observe_db_query()` call, add:
  ```python
  if elapsed > SLOW_QUERY_MS / 1000:
      logger.warning("Slow query (%s): %.1fms", operation, elapsed * 1000)
  ```
- This is purely additive — no change to query execution, no change to return values
- **Do NOT change**: Connection pooling, transaction management, query execution paths

## Validation
- [ ] `ruff check` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -x` — full suite passes
- [ ] Unit test: mock Claude API response with `usage` field, verify `budget_tracking` records actual token counts
- [ ] Unit test: mock query with elapsed > threshold, verify `logger.warning` fires
- [ ] Verify Ollama path still uses estimate (no `usage` field in Ollama responses)
- [ ] `git diff` shows only `shared/llm_client.py` and `shared/db.py` modified

## LOC Impact
- Modified: `shared/llm_client.py` (~25 LOC changed)
- Modified: `shared/db.py` (~10 LOC added)
- Net: **+35 LOC**

## Risk: LOW
- Cost estimation change is a calculation improvement with fallback to existing behavior
- Slow query logging is a pure observability addition (one conditional + one log line)
- Both changes are in shared/ utilities with good test coverage
