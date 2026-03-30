# Phase 0a: P0 Bug Fixes + Skill Hardening

**Goal:** Fix Conway wallet critical bugs and harden skill loader trust boundary before any integration work.
**Requirements:** FIX-01, FIX-02, FIX-03, FIX-04
**Depends on:** Nothing (prerequisite phase)
**Feature flag:** N/A (these are prerequisite fixes)

---

## Context

Conway wallet manages real USDC on Base L2. Three P0 bugs were identified during the March 2026 audit. The skill loader (`shared/skill_loader.py`) loads arbitrary markdown files from disk with zero verification — a critical trust boundary violation that must be hardened before Agent DNA injection (Phase 1) adds system-prompt content through this surface.

### Current State (verified from code)

| Bug | File | Status | Notes |
|-----|------|--------|-------|
| FIX-01: Column order | `conway/wallet.py:193-196` | Appears fixed | INSERT now names columns explicitly. Needs round-trip test. |
| FIX-02: Keystore password | `conway/wallet.py:270-278` | Appears fixed | `_get_keystore_password()` reads env var, raises if empty. Needs test. |
| FIX-03: JSONB writes | `conway/survival.py:149-154` | Appears fixed | Uses `Jsonb()` wrapper when psycopg available. Needs test. |
| FIX-04: Skill signing | `shared/skill_loader.py:76-78` | **Not started** | `load_skill()` does raw `path.read_text()` with zero verification. |

---

## Tasks

### Task 1: Conway wallet round-trip test (FIX-01)
**File:** `tests/conway/test_wallet_roundtrip.py` (new)
**What:**
- Write a test that creates a wallet via `WalletManager`, verifies the DB INSERT has correct column mapping
- Mock the DB layer, verify the SQL parameters: `(agent_name, "base", address, "agent_name.json")` match schema order `(agent_name, chain, public_address, keystore_ref)`
- Verify `get_or_create_wallet` returns the same wallet on second call (idempotency)
- Verify wallet address format (0x-prefixed, 42 chars)

**Acceptance criteria:**
- [ ] Test creates wallet and reads it back with correct field values
- [ ] Column order in INSERT matches `conway_wallets` schema definition
- [ ] Test passes with `PYTHONPATH=. pytest tests/conway/test_wallet_roundtrip.py -v`

### Task 2: Keystore password enforcement test (FIX-02)
**File:** `tests/conway/test_keystore_security.py` (new)
**What:**
- Test `_get_keystore_password()` raises `RuntimeError` when `CONWAY_KEYSTORE_PASSWORD` env var is unset/empty
- Test `_get_keystore_password()` returns the env var value when set
- Test `_create_new_wallet()` calls `Account.encrypt()` with the env-var password (not agent name)
- Test `_load_from_keystore()` calls `Account.decrypt()` with the env-var password (not agent name)

**Acceptance criteria:**
- [ ] Test proves keystore cannot be decrypted with agent name as password
- [ ] Test proves empty env var raises RuntimeError
- [ ] Test passes with `PYTHONPATH=. pytest tests/conway/test_keystore_security.py -v`

### Task 3: JSONB write wrapper test (FIX-03)
**File:** `tests/conway/test_survival_jsonb.py` (new)
**What:**
- Test that `_apply_tier_change()` wraps the tier value in `Jsonb()` before INSERT
- Test that tier values written to `system_config` can be read back correctly
- Mock psycopg's `Jsonb` type and verify it's called
- Test the fallback path when `Jsonb` is not importable (raw string)

**Acceptance criteria:**
- [ ] Test proves JSONB wrapper is used when psycopg is available
- [ ] Test proves fallback works when psycopg Jsonb unavailable
- [ ] Tier value round-trips correctly (write "critical", read back "critical")

### Task 4: Implement signed skill manifests (FIX-04)
**Files:**
- `shared/skill_loader.py` — Add signature verification
- `shared/skill_signer.py` (new) — Signing utility for operator use
- `scripts/generate-skill-signing-key.sh` (new) — One-time key generation

**What:**

#### 4a. Key generation
- Generate Ed25519 keypair using `cryptography` library
- Private key: `~/.objective-hertz/skill-signing-key.pem` (operator keeps)
- Public key: `shared/skill_verify_key.pem` (checked into repo)
- Script: `scripts/generate-skill-signing-key.sh` runs `python -c "..."` to create keypair

#### 4b. Signing utility (`shared/skill_signer.py`)
- `sign_skill(skill_path: Path, private_key_path: Path) -> None`
- Reads skill content, computes Ed25519 signature over content bytes
- Writes signature to `{skill_path}.sig` (adjacent file)
- CLI: `python -m shared.skill_signer sign path/to/SKILL.md`

#### 4c. Verification in loader (`shared/skill_loader.py`)
- Add `verify_skill(skill_path: Path) -> bool` function
- Reads `{skill_path}.sig`, verifies Ed25519 signature against `shared/skill_verify_key.pem`
- Modify `load_skill()` to call `verify_skill()` before returning content
- If verification fails: log error, return empty string (refuse to load)
- If `.sig` file missing: log warning, refuse to load
- Environment override: `SKILL_LOADER_ALLOW_UNSIGNED=true` for development only (logged as warning)

#### 4d. Sign existing skills
- Run signer against all existing SKILL.md files in the skill directories
- Generate `.sig` files for each

**Acceptance criteria:**
- [ ] `load_skill()` rejects unsigned skill files (returns empty, logs error)
- [ ] `load_skill()` accepts properly signed skill files
- [ ] Tampered skill (modified after signing) is rejected
- [ ] `SKILL_LOADER_ALLOW_UNSIGNED=true` bypasses verification (with warning log)
- [ ] Ed25519 keypair generation works on macOS ARM64

### Task 5: Integration test suite
**File:** `tests/conway/test_phase0a_integration.py` (new)
**What:**
- End-to-end test: create wallet → check tier → verify JSONB write → read back
- End-to-end test: sign skill → load skill → execute skill → verify signature checked
- Verify all FIX-01 through FIX-04 acceptance criteria in one test run

**Acceptance criteria:**
- [ ] `PYTHONPATH=. pytest tests/conway/ -v` passes all tests
- [ ] `ruff check conway/ shared/skill_loader.py shared/skill_signer.py` clean

---

## Success Criteria (from ROADMAP.md)

- [ ] Wallet round-trip test: create, write, read back, verify column correctness
- [ ] Keystore cannot be decrypted with agent name
- [ ] Survival tier JSONB writes persist and read back correctly
- [ ] Skill loader rejects unsigned files, accepts signed ones

## Dependencies on Later Phases

- Phase 1 (Agent DNA) depends on FIX-04: DNA profiles loaded through skill loader must be signed
- Phase 6 (PQC) depends on FIX-01/FIX-02: wallet bugs must be fixed before adding quantum-safe crypto

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| FIX-01/02/03 already fixed, wasted effort | High | Low | Tests still add value as regression guards |
| `cryptography` lib conflicts with existing deps | Low | Medium | Already in use by eth_account; verify version compatibility |
| Signing breaks existing skill loading in dev | Medium | Medium | `SKILL_LOADER_ALLOW_UNSIGNED` env override for dev mode |

---

*Plan created: 2026-03-29*
