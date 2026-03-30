# Phase 3: Silent Exception Hardening

## Goal
Add logging to ~20 locations where exceptions are silently swallowed, making failures visible without changing control flow.

## Tasks

### 3.1 Audit and catalog all silent exception locations
- Search patterns: `except Exception: pass`, `except Exception as e: pass`, `except: pass`, `except Exception:\n.*pass`
- Focus on production code (exclude tests/, openjarvis/evals/)
- For each location, classify as:
  - **Hot path** (daemon loops, pipeline stages) — add `logger.warning()`
  - **Cold path** (startup, shutdown, cleanup) — add `logger.debug()`
  - **Intentional** (documented reason for silence) — leave as-is with comment

### 3.2 Add logging to hot path exceptions
Priority locations from discovery:
- `orchestrator.py:280-283` — budget guard init failure
- `orchestrator.py:336-337` — vassal probe failure
- `shared/llm_client.py:242-243` — spend recording failure (already logged as non-critical — verify)
- `shared/agent_base.py` — event forwarding, Conway wallet provisioning
- `conway/` — wallet operations, ledger recording
- `clawdbot/` — capability resolver, brain routing

For each: add exactly ONE line:
```python
except Exception as e:
    logger.warning("Context: %s", e)
    # ... existing code (pass/continue/return) unchanged
```

### 3.3 Add logging to cold path exceptions
- Daemon shutdown handlers
- Connection cleanup
- One-time initialization failures

For each: add `logger.debug()` (not warning — these are expected during cleanup)

### 3.4 Document intentionally silent exceptions
- If a `pass` is truly correct (e.g., checking if optional service is available), add a comment:
  ```python
  except Exception:
      pass  # Expected: service may not be running
  ```

## Rules
- **NEVER change return values** — if the except block returns None, it still returns None
- **NEVER change control flow** — if the except block continues a loop, it still continues
- **NEVER add re-raises** — if the exception was swallowed, it stays swallowed
- **NEVER narrow exception types** — changing `except Exception` to `except ValueError` changes behavior
- Only ADD logging statements

## Validation
- [ ] `ruff check` passes
- [ ] `PYTHONPATH=. python3 -m pytest tests/ -x` — full suite passes
- [ ] `git diff` shows only added lines (no deletions, no modified lines)
- [ ] `grep -rn "except.*pass$" --include="*.py"` in production dirs — count decreases to near-zero
- [ ] Each modified except block reviewed: return/continue/pass behavior identical to before

## LOC Impact
- Added: ~25 logging lines
- Net: **+25 LOC**

## Risk: LOW
- Pure additions — no existing lines modified or deleted
- Logging calls cannot change program behavior
- Each change is one line, independently verifiable
