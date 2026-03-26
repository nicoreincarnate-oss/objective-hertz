---
description: Test Hermes interface (Telegram, alerts, dashboard API)
---

# Test Hermes Interface

## Purpose
Targeted testing of the Hermes daemon — Telegram bot, alerts, and web dashboard.

## Instructions
1. Compile check all Hermes files:
   ```bash
   for f in hermes/*.py hermes/web/*.py; do
     python3 -m py_compile "$f" && echo "OK: $f" || echo "FAIL: $f"
   done
   ```
2. Run Hermes-specific tests:
   ```bash
   PYTHONPATH=. python3 -m pytest tests/ -v -k "hermes or dashboard or alert or telegram"
   ```
3. Check dashboard API: verify hermes/web/app.py endpoints
4. Run ruff on Hermes code: `ruff check hermes/`
5. If dashboard frontend exists, check build: `cd hermes/web/frontend && pnpm build`

## Output
- Compile check results
- Test results
- API endpoint validation
- Ruff results
- Frontend build status (if applicable)
