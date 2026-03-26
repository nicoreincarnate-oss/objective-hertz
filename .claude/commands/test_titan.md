---
description: Test Titan revenue engine pipeline stages
---

# Test Titan Pipeline

## Purpose
Targeted testing of the Titan revenue engine and its 10-stage pipeline.

## Instructions
1. Compile check all Titan files:
   ```bash
   for f in titan/*.py titan/pipeline/*.py; do
     python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"
   done
   ```
2. Run Titan-specific tests:
   ```bash
   PYTHONPATH=. python3 -m pytest tests/ -v -k "titan or pipeline or budget or compliance or state_machine or deliverability or email or lead"
   ```
3. Check state machine transitions: verify titan/state_machine.py has valid transitions
4. Check budget guard: verify tools/budget_guard.py enforces $800/mo limit
5. Run ruff on Titan code: `ruff check titan/`

## Output
- Compile check results per file
- Test results (pass/fail counts)
- State machine validation
- Budget guard validation
- Ruff results
