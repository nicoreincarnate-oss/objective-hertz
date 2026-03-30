---
phase: 00A-p0-bug-fixes-skill-hardening
verified: 2026-03-29T20:15:00Z
status: gaps_found
score: 3/5 must-haves verified
gaps:
  - truth: "Survival tier JSONB writes persist and read back correctly"
    status: failed
    reason: "All 9 tests in test_survival_jsonb.py error at fixture setup — the mock_db_calls fixture patches conway.survival.fetch_one which is not a module-level import in conway/survival.py (only execute, insert_task, set_config are imported). AttributeError at patch time causes every test to fail before executing."
    artifacts:
      - path: "tests/conway/test_survival_jsonb.py"
        issue: "Fixture patches conway.survival.fetch_one which does not exist in that namespace. All 9 tests error at setup, not at assertion."
    missing:
      - "Remove 'conway.survival.fetch_one' from the mock_db_calls fixture — conway/survival.py does not import fetch_one at module level"
      - "Re-run tests after fixture fix to confirm tier/JSONB logic assertions actually pass"

  - truth: "Keystore cannot be decrypted with agent name"
    status: partial
    reason: "4 of 5 keystore tests pass. test_decrypt_uses_env_password FAILS: the mock returns bytes.fromhex('bb' * 32) which produces a 64-char hex string without 0x prefix when .hex() is called — AgentWallet.__init__ requires '0x' prefix, causing ValueError. The production code in conway/wallet.py correctly uses the env password (line 261 calls _get_keystore_password via _load_from_keystore), but the test is not runnable."
    artifacts:
      - path: "tests/conway/test_keystore_security.py"
        issue: "test_decrypt_uses_env_password: mock_account.decrypt.return_value = bytes.fromhex('bb' * 32) produces a plain hex string when .hex() is called (no 0x prefix). AgentWallet constructor requires private_key to start with '0x' and be 66 chars."
    missing:
      - "Fix mock: use HexBytes or patch the AgentWallet constructor — or use return_value that produces a 66-char 0x-prefixed string after .hex() call"
human_verification: []
---

# Phase 00A: P0 Bug Fixes + Skill Hardening Verification Report

**Phase Goal:** Fix Conway wallet critical bugs and harden skill loader trust boundary before any integration work.
**Verified:** 2026-03-29T20:15:00Z
**Status:** GAPS FOUND
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Wallet round-trip test: create, write, read back, verify column correctness | VERIFIED | 3/3 tests in test_wallet_roundtrip.py pass: column order asserted in SQL, idempotency confirmed, address format validated |
| 2 | Keystore cannot be decrypted with agent name | PARTIAL | 4/5 tests pass. test_decrypt_uses_env_password FAILS with ValueError from AgentWallet.__init__ due to mock returning raw hex bytes without 0x prefix |
| 3 | Survival tier JSONB writes persist and read back correctly | FAILED | All 9 tests ERROR at setup — fixture patches conway.survival.fetch_one which is not a module-level attribute |
| 4 | Skill loader rejects unsigned files, accepts signed ones | VERIFIED | All 8 tests in test_skill_signing.py pass: sign creates 64-byte .sig, tamper rejected, missing sig rejected, load_skill returns "" for unsigned, returns content for signed, ALLOW_UNSIGNED bypass works |

**Score:** 2/4 truths fully verified (1 partial, 1 failed)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `tests/conway/test_wallet_roundtrip.py` | FIX-01 regression tests | VERIFIED | 3 tests, all pass. Column order assertion present: "INSERT INTO conway_wallets (agent_name, chain, public_address, keystore_ref)" |
| `tests/conway/test_keystore_security.py` | FIX-02 regression tests | PARTIAL | 5 tests exist, 4 pass, 1 fails (test_decrypt_uses_env_password) |
| `tests/conway/test_survival_jsonb.py` | FIX-03 regression tests | STUB | 9 tests defined but all error at fixture setup — not runnable |
| `shared/skill_signer.py` | Ed25519 signing utility | VERIFIED | sign_skill(), sign_all_skills(), CLI — substantive, 95 lines |
| `shared/skill_loader.py` | Verification-enabled skill loader | VERIFIED | verify_skill() and load_skill() gate present, VERIFY_KEY_PATH module var, SKILL_LOADER_ALLOW_UNSIGNED check |
| `shared/skill_verify_key.pem` | Ed25519 public key | VERIFIED | PEM file present, contains "-----BEGIN PUBLIC KEY-----" |
| `scripts/generate-skill-signing-key.sh` | Key generation script | VERIFIED | File exists, contains Ed25519 generation logic |
| `tests/conway/test_skill_signing.py` | FIX-04 regression tests | VERIFIED | 8 tests, all pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| tests/conway/test_wallet_roundtrip.py | conway/wallet.py | imports WalletManager | WIRED | `from conway.wallet import WalletManager` present; patches conway.wallet.execute correctly |
| tests/conway/test_keystore_security.py | conway/wallet.py | imports _get_keystore_password | WIRED | `from conway.wallet import _get_keystore_password` present |
| tests/conway/test_survival_jsonb.py | conway/survival.py | imports SurvivalMonitor | BROKEN | Import of SurvivalMonitor is present but mock fixture patches a non-existent attribute (conway.survival.fetch_one) causing all tests to fail at setup |
| shared/skill_loader.py | shared/skill_verify_key.pem | reads public key at VERIFY_KEY_PATH | WIRED | VERIFY_KEY_PATH = Path(__file__).parent / "skill_verify_key.pem" |
| shared/skill_signer.py | shared/skill_loader.py | creates .sig files that loader verifies | WIRED | sign_skill() writes .sig; verify_skill() reads .sig |
| shared/skill_loader.py | SKILL_LOADER_ALLOW_UNSIGNED | env var bypass check | WIRED | os.environ.get("SKILL_LOADER_ALLOW_UNSIGNED", "").lower() == "true" |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Wallet tests pass | pytest tests/conway/test_wallet_roundtrip.py | 3 passed | PASS |
| Keystore tests pass | pytest tests/conway/test_keystore_security.py | 4 passed, 1 failed | FAIL |
| JSONB tests pass | pytest tests/conway/test_survival_jsonb.py | 9 errors (setup failure) | FAIL |
| Skill signing tests pass | pytest tests/conway/test_skill_signing.py | 8 passed | PASS |
| verify_skill imports cleanly | python3 -c "from shared.skill_loader import verify_skill, load_skill" | success | PASS |
| skill_signer imports cleanly | python3 -c "from shared.skill_signer import sign_skill" | success | PASS |
| Public key file present and valid PEM | cat shared/skill_verify_key.pem | "-----BEGIN PUBLIC KEY-----" present | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| FIX-01 | 00A-01 | Wallet INSERT column order matches schema | SATISFIED | test_insert_column_order passes, asserts exact column order in SQL |
| FIX-02 | 00A-01 | Keystore uses env-var password, not agent name | PARTIAL | 4/5 tests prove it; test_decrypt_uses_env_password broken (mock bug, not prod bug) |
| FIX-03 | 00A-01 | Survival JSONB writes | BLOCKED | All 9 tests non-functional due to fixture error |
| FIX-04 | 00A-02 | Skill loader rejects unsigned/tampered files | SATISFIED | 8/8 tests pass covering all FIX-04 acceptance criteria |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| tests/conway/test_survival_jsonb.py | 44 | `patch("conway.survival.fetch_one", ...)` — fetch_one not in conway.survival namespace | BLOCKER | Causes all 9 JSONB tests to error at setup; FIX-03 has zero passing tests |
| tests/conway/test_keystore_security.py | 82 | `mock_account.decrypt.return_value = bytes.fromhex("bb" * 32)` — returns bytes that .hex() produces without 0x prefix | BLOCKER | AgentWallet.__init__ requires "0x" prefix on private_key; test crashes before reaching the decrypt-uses-env-password assertion |

### Human Verification Required

None — all gaps are programmatically verifiable and have been identified.

---

## Root Cause Analysis

**FIX-03 gap (JSONB test fixture):** `conway/survival.py` imports `from shared.db import execute, insert_task, set_config` — there is no `fetch_one` import. The `check_all_agents` method imports `fetch_all` locally (inside the function body), not at module level. The test fixture's `patch("conway.survival.fetch_one", ...)` fails immediately with `AttributeError: <module 'conway.survival'> does not have the attribute 'fetch_one'`. This is a pure test-authoring error — the production JSONB write code in `_apply_tier_change` (lines 148-153) uses `_JsonbType` correctly and is not itself broken.

**FIX-02 gap (decrypt test mock):** `conway/wallet.py` line 261: `return AgentWallet(agent_name, address, private_key.hex())`. The mock returns `bytes.fromhex("bb" * 32)` which when `.hex()` is called gives `"bb" * 32` (64 chars, no `0x` prefix). `AgentWallet.__init__` validates `private_key.startswith("0x") and len(private_key) == 66`. The fix is a one-line mock correction.

## Gaps Summary

Two test files have blocking bugs preventing them from running:

1. **test_survival_jsonb.py** — The `mock_db_calls` fixture references `conway.survival.fetch_one` which is not a module-level attribute. All 9 FIX-03 tests are non-functional. The underlying production code appears correct but is unproven.

2. **test_keystore_security.py** — One of five FIX-02 tests (`test_decrypt_uses_env_password`) has a mock setup error: the `decrypt` mock returns bytes that, after `.hex()`, lack the required `0x` prefix. The production keystore security behavior IS implemented correctly in `conway/wallet.py` but this specific test cannot confirm it.

Both gaps are test-side bugs — the fixes are small (remove one patch call from the fixture, fix one mock return value). Neither involves changes to production code.

---

_Verified: 2026-03-29T20:15:00Z_
_Verifier: Claude (gsd-verifier)_
