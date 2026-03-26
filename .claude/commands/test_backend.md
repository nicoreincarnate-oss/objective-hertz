---
description: Run and validate Perseus test suite
---

# Test Backend (Perseus)

## Purpose
Run the full Perseus test suite with Python quality checks.

## Instructions
1. Run ruff linting: `ruff check shared perseus titan hermes clawdbot tests`
2. Run type checking: `python3 -m mypy shared perseus titan hermes clawdbot` (if configured)
3. Run tests: `PYTHONPATH=. python3 -m pytest tests/ -v --ignore=tests/openjarvis`
4. If tests fail:
   a. Identify the failing tests
   b. Read the error messages
   c. Attempt to fix the underlying code (not the tests)
   d. Rerun failing tests
   e. If still failing after 2 attempts, report the failures
5. Report results

## Output
- Ruff results (clean / N issues)
- Test totals: Passed / Failed / Skipped
- Details on any failures
- Any fixes applied
