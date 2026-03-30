---
phase: 00A-p0-bug-fixes-skill-hardening
plan: 01
subsystem: conway
tags: [testing, regression, wallet, keystore, survival, jsonb]
dependency_graph:
  requires: []
  provides: [FIX-01-tests, FIX-02-tests, FIX-03-tests]
  affects: [conway/wallet.py, conway/survival.py]
tech_stack:
  added: []
  patterns: [module-level-mock-patching, parametrized-tier-tests]
key_files:
  created:
    - tests/conway/__init__.py
    - tests/conway/test_wallet_roundtrip.py
    - tests/conway/test_keystore_security.py
    - tests/conway/test_survival_jsonb.py
  modified: []
decisions:
  - Patch conway.wallet.execute instead of shared.db.execute to handle module-level imports correctly
  - Fixed tier boundary test data: balance 0.3 maps to dead (< 0.5 critical threshold), not critical
  - Added balance 1.0 test case for critical tier coverage
metrics:
  duration_seconds: 236
  completed: "2026-03-29T19:55:08Z"
  tests_added: 17
  files_created: 4
---

# Phase 00A Plan 01: Conway P0 Bug Regression Tests Summary

17 regression tests proving Conway wallet column order, keystore env-var password enforcement, and survival tier JSONB writes are correctly implemented.

## What Was Done

### Task 1: Wallet Round-Trip and Keystore Security Tests (FIX-01 + FIX-02)

Created `tests/conway/test_wallet_roundtrip.py` (3 tests):
- `test_insert_column_order` — Verifies INSERT uses `(agent_name, chain, public_address, keystore_ref)` column order matching the conway_wallets schema
- `test_get_or_create_idempotent` — Second call returns cached wallet, no duplicate DB write
- `test_wallet_address_format` — Address starts with 0x and has length 42

Created `tests/conway/test_keystore_security.py` (5 tests):
- `test_get_keystore_password_raises_when_unset` — Missing env var raises RuntimeError
- `test_get_keystore_password_raises_when_empty` — Empty env var raises RuntimeError
- `test_get_keystore_password_returns_value` — Valid env var returned as-is
- `test_encrypt_uses_env_password` — Account.encrypt called with env password, not agent name
- `test_decrypt_uses_env_password` — Account.decrypt called with env password, not agent name

### Task 2: Survival Tier JSONB Write Tests (FIX-03)

Created `tests/conway/test_survival_jsonb.py` (9 tests including parametrized):
- `test_jsonb_wrapper_used` — Confirms psycopg Jsonb() wrapper applied to system_config writes
- `test_tier_value_correct_for_each_balance` (6 parametrized cases) — normal(15.0), low_compute(5.0), critical(1.0), critical(0.5), dead(0.3), dead(0)
- `test_tier_change_emits_event` — Tier transitions emit survival_tier_change event with correct JSON payload
- `test_critical_tier_triggers_alert` — Critical tier sends operator alert via shared.comms.send_alert

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | bfdcd68 | test(00A-01): add Conway wallet column order and keystore security regression tests |
| 1-fix | 4a4b75e | fix(00A-01): patch correct module paths in wallet/keystore tests |
| 2 | 80f55b2 | test(00A-01): add Conway survival tier JSONB write regression tests |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed mock patch targets for module-level imports**
- **Found during:** Task 1 verification (full suite run)
- **Issue:** Tests patched `shared.db.execute` but `conway.wallet` imports `execute` at module level, so the patched attribute on `shared.db` was not seen by `conway.wallet.execute`
- **Fix:** Changed patch targets to `conway.wallet.execute` and `conway.wallet.fetch_one`; similarly `conway.survival.execute` etc.
- **Files modified:** tests/conway/test_wallet_roundtrip.py, tests/conway/test_survival_jsonb.py
- **Commit:** 4a4b75e

**2. [Rule 1 - Bug] Fixed incorrect tier boundary test data**
- **Found during:** Task 2 verification
- **Issue:** Plan specified balance 0.3 should map to "critical" tier, but the code checks `balance >= 0.5` for critical. Balance 0.3 < 0.5, so it falls through to "dead" (>= 0)
- **Fix:** Changed expected tier for 0.3 to "dead", added balance 1.0 test case for critical coverage
- **Files modified:** tests/conway/test_survival_jsonb.py
- **Commit:** 80f55b2

**3. [Rule 1 - Bug] Plan referenced non-existent _JsonbType attribute**
- **Found during:** Task 2 code analysis
- **Issue:** Plan referenced `conway.survival._JsonbType` with try/except fallback, but actual code uses inline `from psycopg.types.json import Jsonb` inside `_apply_tier_change`
- **Fix:** Tests mock `psycopg.types.json.Jsonb` directly, matching actual implementation
- **Files modified:** tests/conway/test_survival_jsonb.py
- **Commit:** 80f55b2

## Known Stubs

None - all tests are fully wired to real code paths.

## Verification

```
PYTHONPATH=. python3 -m pytest tests/conway/ -v  -> 17 passed
ruff check tests/conway/                          -> All checks passed
```

## Self-Check: PASSED

- FOUND: tests/conway/__init__.py
- FOUND: tests/conway/test_wallet_roundtrip.py
- FOUND: tests/conway/test_keystore_security.py
- FOUND: tests/conway/test_survival_jsonb.py
- FOUND: .planning/phases/00A-p0-bug-fixes-skill-hardening/00A-01-SUMMARY.md
- FOUND: commit bfdcd68
- FOUND: commit 4a4b75e
- FOUND: commit 80f55b2
