# Phase 6: Post-Quantum Cryptography

**Goal:** Conway wallet keys and API secrets protected against harvest-now-decrypt-later quantum attacks with hybrid encryption.
**Requirements:** PQC-01, PQC-02, PQC-03, PQC-04, PQC-05, PQC-06, PQC-07, PQC-08
**Depends on:** Phase 0a (Conway P0 bugs fixed)
**Feature flag:** `PQC_ENABLED`

---

## Context

Conway wallets hold real USDC on Base L2. The "harvest now, decrypt later" threat means encrypted keystores and API secrets need quantum-safe protection today. PQC uses hybrid encryption: ML-KEM-768 (NIST standard, quantum-safe key encapsulation) combined with AES-256 (classical, proven). ML-DSA-65 provides quantum-safe transaction signatures.

Critical prerequisite: ARM64 validation spike. liboqs-python must work on M4 Mac before committing to full implementation. If it fails, fall back to `pqcrypto` pure-Python library.

### Current State

| Component | File | Status |
|-----------|------|--------|
| Wallet keystore | `conway/wallet.py:227-268` | AES via eth_account.encrypt() — classical only |
| Keystore password | `conway/wallet.py:270-278` | Single shared `CONWAY_KEYSTORE_PASSWORD` |
| Secret management | `.env` + `os.environ.get()` | Plaintext in .env file |
| PQC libraries | None | Not installed |
| encrypted_keys table | None | Does not exist |

---

## Tasks

### Task 1: ARM64 validation spike (PQC-01)
**File:** `scripts/pqc-validation-spike.py` (new, throwaway)
**What:**

2-day timeboxed validation:
1. Install `liboqs-python` on M4 Mac: `pip install liboqs-python`
2. If fails: try `pqcrypto`: `pip install pqcrypto`
3. If both fail: try `oqs` C library via cffi bindings
4. Test operations:
   - ML-KEM-768 keypair generation
   - Key encapsulation/decapsulation
   - ML-DSA-65 signing and verification
   - Performance: keygen < 100ms, encap/decap < 50ms, sign/verify < 50ms

Decision gate:
- **PASS**: Library works on ARM64, performance acceptable → proceed with Phase 6
- **FAIL**: No library works → defer Phase 6, file issue, use classical crypto with rotation

**Acceptance criteria:**
- [ ] Validation spike script runs on M4 Mac
- [ ] At least one library produces ML-KEM-768 keypairs
- [ ] Performance benchmarks documented
- [ ] Go/no-go decision recorded in `.planning/phases/6/SPIKE-RESULT.md`

### Task 2: Hybrid ML-KEM-768 + AES-256 encryption (PQC-02)
**File:** `conway/pqc.py` (new)
**What:**

```python
class HybridEncryptor:
    """Hybrid quantum-safe encryption: ML-KEM-768 + AES-256-GCM.

    Key encapsulation: ML-KEM-768 generates shared secret
    Data encryption: AES-256-GCM with ML-KEM shared secret as key
    """

    def __init__(self, pqc_public_key: bytes, classical_key: bytes):
        self._pqc_pub = pqc_public_key
        self._classical = classical_key

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt with hybrid scheme.

        1. ML-KEM-768 encapsulate → (ciphertext_kem, shared_secret)
        2. Derive AES key from shared_secret via HKDF
        3. AES-256-GCM encrypt plaintext
        4. Return: kem_ciphertext || nonce || aes_ciphertext || tag
        """
        ...

    def decrypt(self, ciphertext: bytes, pqc_private_key: bytes) -> bytes:
        """Decrypt with hybrid scheme.

        1. Extract kem_ciphertext from header
        2. ML-KEM-768 decapsulate → shared_secret
        3. Derive AES key from shared_secret via HKDF
        4. AES-256-GCM decrypt
        """
        ...
```

Implements `CryptoProvider` Protocol from Phase 0b.

**Acceptance criteria:**
- [ ] Encrypt/decrypt round-trip successful
- [ ] Uses ML-KEM-768 for key encapsulation
- [ ] Uses AES-256-GCM for data encryption
- [ ] `isinstance(HybridEncryptor(...), CryptoProvider)` passes

### Task 3: ML-DSA-65 transaction signatures (PQC-03)
**File:** `conway/pqc.py` (extend)
**What:**

```python
class QuantumSafeSigner:
    """ML-DSA-65 digital signatures for Conway wallet transactions."""

    def __init__(self, signing_key: bytes):
        self._key = signing_key

    def sign(self, message: bytes) -> bytes:
        """Sign transaction data with ML-DSA-65."""
        ...

    def verify(self, message: bytes, signature: bytes, public_key: bytes) -> bool:
        """Verify ML-DSA-65 signature."""
        ...
```

Integrate with `conway/wallet.py` `send_usdc()` method — sign transaction before broadcast.

**Acceptance criteria:**
- [ ] Transaction signatures verify with ML-DSA-65
- [ ] Signature attached to every USDC transfer
- [ ] Invalid signatures rejected

### Task 4: SecureConfig for .env encryption (PQC-04)
**File:** `shared/secure_config.py` (new)
**What:**

```python
class SecureConfig:
    """Encrypts .env secrets at rest using hybrid PQC encryption.

    On first run: reads .env, encrypts each secret, writes .env.encrypted
    On subsequent runs: decrypts .env.encrypted → provides values via get()
    """

    def __init__(self, env_path: Path = Path(".env")):
        self._env_path = env_path
        self._encrypted_path = env_path.with_suffix(".env.encrypted")
        self._decrypted: dict[str, str] = {}

    def encrypt_env(self) -> None:
        """One-time: encrypt all .env secrets."""
        ...

    def get(self, key: str, default: str = "") -> str:
        """Get decrypted config value."""
        if not self._decrypted:
            self._load_and_decrypt()
        return self._decrypted.get(key, default)
```

**Acceptance criteria:**
- [ ] .env secrets encrypted at rest
- [ ] `SecureConfig.get()` transparently decrypts
- [ ] Original .env can be removed after encryption

### Task 5: Dual-key migration table (PQC-05)
**File:** `scripts/migrations/022-pqc-keys.sql` (new)
**What:**

```sql
CREATE TABLE IF NOT EXISTS encrypted_keys (
    id SERIAL PRIMARY KEY,
    agent_name TEXT NOT NULL,
    key_type TEXT NOT NULL CHECK (key_type IN ('classical', 'pqc', 'hybrid')),
    public_key BYTEA NOT NULL,
    encrypted_private_key BYTEA NOT NULL,
    algorithm TEXT NOT NULL,  -- 'AES-256', 'ML-KEM-768', 'hybrid'
    valid_from TIMESTAMPTZ DEFAULT NOW(),
    valid_until TIMESTAMPTZ,  -- NULL = current, set date = deprecated
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (agent_name, key_type, valid_from)
);

CREATE INDEX idx_encrypted_keys_agent ON encrypted_keys (agent_name, key_type);
```

Dual-key period: both classical and PQC keys work simultaneously for 30 days, then classical keys expire.

**Acceptance criteria:**
- [ ] Both legacy and PQ keys work simultaneously
- [ ] Classical keys expire after 30-day transition
- [ ] Key type tracked per agent

### Task 6: Per-agent key derivation (PQC-06)
**File:** `conway/pqc.py` (extend)
**What:**

Replace single shared `CONWAY_KEYSTORE_PASSWORD` with per-agent key derivation:

```python
def derive_agent_key(agent_name: str, master_password: str) -> bytes:
    """Derive unique encryption key per agent from master password.

    Uses HKDF with agent_name as context.
    """
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes

    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"objective-hertz-conway",
        info=agent_name.encode(),
    ).derive(master_password.encode())
```

**Acceptance criteria:**
- [ ] Each agent gets a unique derived key
- [ ] Single `CONWAY_KEYSTORE_PASSWORD` still used as master input
- [ ] Different agents cannot decrypt each other's keystores

### Task 7: Key rotation mechanism (PQC-07)
**File:** `conway/pqc.py` (extend)
**What:**

```python
async def rotate_keys(agent_name: str) -> None:
    """Generate new PQ keys without losing access to funds.

    1. Generate new ML-KEM-768 keypair
    2. Re-encrypt wallet private key with new PQ key
    3. Store new key in encrypted_keys table
    4. Mark old key with valid_until = NOW() + 30 days
    5. Both keys valid during transition period
    """
    ...
```

**Acceptance criteria:**
- [ ] Key rotation produces new PQ keys
- [ ] Old key remains valid for 30-day transition
- [ ] Wallet access maintained throughout rotation

### Task 8: Feature flag + tests (PQC-08)
**Files:**
- `tests/conway/test_pqc.py` (new)
- `tests/shared/test_secure_config.py` (new)

**What:**
- `PQC_ENABLED=false` → classical encryption (existing behavior)
- `PQC_ENABLED=true` → hybrid PQC encryption
- Test encrypt/decrypt round-trip
- Test ML-DSA-65 signature verify
- Test dual-key period (both keys work)
- Test per-agent key derivation (agent A can't decrypt agent B)
- Test key rotation
- Test SecureConfig encrypt/decrypt .env

**Acceptance criteria:**
- [ ] Feature flag toggles between classical and PQC
- [ ] All tests pass with `PYTHONPATH=. pytest tests/conway/test_pqc.py tests/shared/test_secure_config.py -v`

---

## Success Criteria (from ROADMAP.md)

- [ ] ARM64 validation spike passes (liboqs-python or pqcrypto works on M4)
- [ ] Wallet key encrypt/decrypt round-trip successful
- [ ] Transaction signatures verify with ML-DSA-65
- [ ] Dual-key period: both legacy and PQ keys work simultaneously
- [ ] Key rotation produces new PQ keys without losing funds
- [ ] .env secrets encrypted at rest via SecureConfig

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| liboqs-python fails on ARM64 | Medium | High | 2-day spike validates first; pqcrypto fallback; pure-Python last resort |
| PQC adds latency to wallet ops | Low | Medium | Keygen cached; encap/decap < 50ms benchmarked in spike |
| Key rotation loses access | Low | Critical | Dual-key 30-day overlap; backup keys stored separately |
| SecureConfig breaks .env loading | Medium | High | Feature flag; fallback to plaintext .env if decryption fails |

---

*Plan created: 2026-03-29*
