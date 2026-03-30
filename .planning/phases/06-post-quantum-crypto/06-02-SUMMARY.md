---
phase: 6
plan: 06-02
subsystem: conway/wallet, tests/conway, tests/shared
tags: [pqc, feature-flag, wallet-integration, testing, security]
dependency_graph:
  requires: [conway/pqc.py, shared/secure_config.py, shared/contracts.py]
  provides: [PQC wallet integration, 73-test PQC+SecureConfig suite]
  affects: [conway/wallet.py (PQC-enabled keystore + signing)]
tech_stack:
  added: []
  patterns: [feature-flag gating, graceful import fallback, per-agent key derivation]
key_files:
  created:
    - tests/conway/test_pqc.py
    - tests/shared/test_secure_config.py
    - tests/conway/__init__.py
    - tests/shared/__init__.py
  modified:
    - conway/wallet.py
    - conway/pqc.py
decisions:
  - "PQC signer uses HKDF-derived agent key as Ed25519 seed (deterministic per agent)"
  - "PQC keystore wrapping stores marker JSON with pqc_wrapped:true for transparent detection"
  - "PQC signatures on USDC transfers are non-fatal audit trail (warning on failure)"
metrics:
  duration: 5min
  completed: "2026-03-30T00:47:00Z"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 73
  files_changed: 6
---

# Phase 6 Plan 02: Feature Flag + Tests Summary

PQC wired into Conway wallet behind PQC_ENABLED flag with 73-test comprehensive suite covering encryption, signing, key derivation, rotation, and SecureConfig.

## Tasks Completed

### Task 1: Feature flag + wallet integration

Wired PQC into `conway/wallet.py`:

- **Import fallback**: PQC module imported with try/except; when unavailable, `is_pqc_enabled()` stub always returns False
- **AgentWallet**: When PQC_ENABLED=true, initializes `QuantumSafeSigner` using HKDF-derived agent key as Ed25519 seed
- **send_usdc()**: PQC signature attached to transaction payload as quantum-safe audit trail (non-fatal on failure)
- **_create_new_wallet()**: When PQC enabled, wraps keystore JSON with HybridEncryptor using per-agent derived key
- **_load_from_keystore()**: Detects `pqc_wrapped:true` marker and decrypts transparently; handles both classical and PQC keystores
- Cleaned up pre-existing unused imports (typing.Any, shared.config.config)

**Commit:** 7fd6ce5

### Task 2: Comprehensive test suite (73 tests)

Created `tests/conway/test_pqc.py` (53 tests) and `tests/shared/test_secure_config.py` (20 tests):

**PQC tests (53):**
- HybridEncryptor (12): round-trip, empty/large payloads, format version, nonce uniqueness, wrong key, tamper detection, short ciphertext, wrong version, invalid key sizes
- QuantumSafeSigner (11): sign/verify, invalid/wrong signatures, explicit public key, cross-signer verify, deterministic seeds, key generation, empty message, signature length
- PQCCryptoProvider (6): Protocol compliance, encrypt/decrypt, sign/verify, explicit signing key, cross-provider failure
- derive_agent_key (7): 32-byte output, determinism, agent isolation, password isolation, encrypt/decrypt isolation, empty input guards
- is_pqc_enabled (8): default disabled, true/1/yes/TRUE enabled, false/0/random disabled
- rotate_keys (4): return fields, DB UPDATE+INSERT, no password error, 30-day dual-key overlap
- get_active_key (2): None when no keys, dict when key exists
- Wallet integration (3): PQC disabled no signer, PQC enabled with password, import fallback

**SecureConfig tests (20):**
- encrypt_env (8): file creation, valid JSON, bootstrap plaintext, secret encryption, comment skipping, missing .env, missing password, quoted values
- get (6): round-trip, all values, missing key default, environ fallback, precedence, wrong password fallback
- keys (2): list all, empty when no file
- Edge cases (4): equals in value, no password env var, encrypted_path property, reencrypt overwrite

**Commit:** c5dfe3c

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed Ed25519PublicKey.from_raw_bytes -> from_public_bytes**
- **Found during:** Task 2 (test_verify_with_explicit_public_key)
- **Issue:** `conway/pqc.py` line 175 used `Ed25519PublicKey.from_raw_bytes()` which does not exist in the `cryptography` library
- **Fix:** Changed to `Ed25519PublicKey.from_public_bytes()` (the correct API)
- **Files modified:** conway/pqc.py
- **Commit:** c5dfe3c

**2. [Rule 1 - Bug] Fixed unused imports in wallet.py**
- **Found during:** Task 1 (ruff check)
- **Issue:** `typing.Any` and `shared.config.config` were imported but unused
- **Fix:** Removed unused imports
- **Files modified:** conway/wallet.py
- **Commit:** 7fd6ce5

## Known Stubs

None -- all PQC functionality is fully wired with graceful fallback.

## Acceptance Criteria

- [x] Feature flag toggles cleanly (8 tests for is_pqc_enabled)
- [x] Conway wallet uses PQC when enabled (keystore encryption + transaction signing)
- [x] All 73 tests pass
- [x] Graceful fallback without PQC libs (import fallback + stub is_pqc_enabled)
