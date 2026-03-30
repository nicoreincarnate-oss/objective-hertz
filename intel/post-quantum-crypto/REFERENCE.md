# Post-Quantum Cryptography — Reference

**Key Libraries:**
- https://github.com/open-quantum-safe/liboqs-python (Python bindings for liboqs)
- https://pypi.org/project/pqcrypto/ (CFFI bindings, v0.4.0, Jan 2026)
- https://github.com/aabmets/quantcrypt (PQClean bindings — deprecated July 2026)

---

## Why This Matters for Objective Hertz

Conway manages Base L2 USDC wallets with private keys (`conway/wallet.py`). OH stores live API keys in `.env` (Stripe secrets, Claude API, Instantly API). The Y2Q threat (harvest-now-decrypt-later) means attackers can steal encrypted data today and decrypt it when quantum computers are powerful enough. SandboxAQ has a $5.75B valuation and a 5-year DoD contract specifically to solve this problem. You can start protecting your keys for free.

**Direct application:** Wrap Conway's key storage and `.env` secret management in post-quantum encryption using liboqs-python or pqcrypto.

---

## The Y2Q Threat

**Harvest Now, Decrypt Later (HNDL):**
- Attackers intercept and store encrypted data TODAY
- When quantum computers can break RSA/ECC (estimated 2030-2035), they decrypt everything
- This means your CURRENT private keys, API secrets, and wallet keys are vulnerable NOW
- Over 20 billion devices globally need post-quantum upgrades

**NSA Timeline:**
- By 2025: Software/firmware signing and TLS must use quantum-resistant algorithms
- By 2026: Network equipment must transition
- By 2035: All systems must use approved PQC

---

## Library 1: liboqs-python (Recommended)

### Installation
```bash
# Requires: cmake, C compiler, git, Python 3
pip install liboqs-python
# Auto-downloads and builds liboqs C library on first use
```

### Core API

**Key Encapsulation (protect shared secrets):**
```python
import oqs

# Key generation
kem = oqs.KeyEncapsulation("ML-KEM-768")  # NIST standardized
public_key = kem.generate_keypair()

# Encryption (by sender)
ciphertext, shared_secret_sender = kem.encap_secret(public_key)

# Decryption (by receiver)
shared_secret_receiver = kem.decap_secret(ciphertext)

# shared_secret_sender == shared_secret_receiver
# Use shared_secret as AES key for symmetric encryption
```

**Digital Signatures (verify authenticity):**
```python
import oqs

sig = oqs.Signature("ML-DSA-65")  # NIST standardized
public_key = sig.generate_keypair()

# Sign
message = b"Transfer 100 USDC to wallet 0x..."
signature = sig.sign(message)

# Verify
is_valid = sig.verify(message, signature, public_key)
```

**Deterministic Key Generation (from seed):**
```python
kem = oqs.KeyEncapsulation("ML-KEM-768")
seed = bytes.fromhex("your_deterministic_seed_here")
public_key = kem.generate_keypair(seed=seed)
```

### Supported Algorithms

**KEM (Key Encapsulation):**
- ML-KEM-512, ML-KEM-768, ML-KEM-1024 (NIST standardized, recommended)
- HQC-128, HQC-192, HQC-256
- BIKE variants

**Signatures:**
- ML-DSA-44, ML-DSA-65, ML-DSA-87 (NIST standardized, recommended)
- Falcon-512, Falcon-1024
- SPHINCS+ variants (SHA2/SHAKE, multiple security levels)

---

## Library 2: pqcrypto (Alternative)

### Installation
```bash
pip install pqcrypto  # v0.4.0, pre-compiled wheels available
# Supports: Python 3.9-3.14, Windows, macOS ARM64, Linux x86-64/ARM64
```

### API
```python
from pqcrypto.kem.ml_kem_768 import generate_keypair, encrypt, decrypt

# Key generation
public_key, secret_key = generate_keypair()

# Encrypt
ciphertext, shared_secret = encrypt(public_key)

# Decrypt
shared_secret_dec = decrypt(secret_key, ciphertext)
```

### Supported Algorithms
**KEM:** HQC (128/192/256), McEliece (10+ variants), ML-KEM (512/768/1024)
**Signatures:** Falcon (512/1024), ML-DSA (44/65/87), SPHINCS+ (many variants)

---

## Integration Pattern for Objective Hertz

### Conway Wallet Key Protection

```python
# In conway/wallet.py

import oqs
import json
from pathlib import Path

class QuantumSafeWallet:
    """Wrap wallet private keys in post-quantum encryption."""

    def __init__(self, wallet_path: Path):
        self.wallet_path = wallet_path
        self.kem = oqs.KeyEncapsulation("ML-KEM-768")

    def generate_master_key(self) -> bytes:
        """Generate PQ-safe master key for wallet encryption."""
        public_key = self.kem.generate_keypair()
        return public_key

    def encrypt_private_key(self, private_key: bytes, pq_public_key: bytes) -> dict:
        """Encrypt a wallet private key with PQ-safe KEM."""
        ciphertext, shared_secret = self.kem.encap_secret(pq_public_key)

        # Use shared_secret as AES-256 key for symmetric encryption
        from cryptography.fernet import Fernet
        import base64
        fernet_key = base64.urlsafe_b64encode(shared_secret[:32])
        f = Fernet(fernet_key)
        encrypted = f.encrypt(private_key)

        return {
            "ciphertext": ciphertext.hex(),
            "encrypted_key": encrypted.decode(),
            "algorithm": "ML-KEM-768+AES-256"
        }

    def decrypt_private_key(self, encrypted_data: dict) -> bytes:
        """Decrypt a wallet private key."""
        ciphertext = bytes.fromhex(encrypted_data["ciphertext"])
        shared_secret = self.kem.decap_secret(ciphertext)

        from cryptography.fernet import Fernet
        import base64
        fernet_key = base64.urlsafe_b64encode(shared_secret[:32])
        f = Fernet(fernet_key)
        return f.decrypt(encrypted_data["encrypted_key"].encode())
```

### API Key Protection (.env secrets)

```python
# In shared/config.py — protect sensitive config values

import oqs
from cryptography.fernet import Fernet
import base64

class SecureConfig:
    """Post-quantum encrypted configuration for sensitive values."""

    SENSITIVE_KEYS = [
        "STRIPE_SECRET_KEY",
        "INSTANTLY_API_KEY",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "CONWAY_WALLET_PRIVATE_KEY",
    ]

    def __init__(self):
        self.kem = oqs.KeyEncapsulation("ML-KEM-768")
        self._load_or_generate_keys()

    def _load_or_generate_keys(self):
        key_path = Path(".pq_keys.json")
        if key_path.exists():
            data = json.loads(key_path.read_text())
            # ... load existing keys
        else:
            self.public_key = self.kem.generate_keypair()
            # ... save keys

    def encrypt_env_value(self, key: str, value: str) -> str:
        if key in self.SENSITIVE_KEYS:
            ciphertext, shared_secret = self.kem.encap_secret(self.public_key)
            fernet_key = base64.urlsafe_b64encode(shared_secret[:32])
            f = Fernet(fernet_key)
            return f.encrypt(value.encode()).decode()
        return value
```

---

## Recommended Algorithms (NIST Standardized)

| Use Case | Algorithm | Security Level |
|----------|-----------|---------------|
| Key exchange / shared secrets | ML-KEM-768 | 192-bit |
| Digital signatures | ML-DSA-65 | 192-bit |
| Small signatures (bandwidth-constrained) | Falcon-512 | 128-bit |
| Stateless signatures (high security) | SPHINCS+-SHA2-192f | 192-bit |

**For OH:** Use ML-KEM-768 for encrypting stored secrets. Use ML-DSA-65 for signing Conway wallet transactions.

---

## Hybrid Approach (Recommended for Production)

Don't replace existing encryption — layer PQ on top:

```
Current: AES-256(private_key)
Hybrid:  AES-256(ML-KEM-768(private_key))

If quantum breaks ML-KEM-768: AES-256 still protects
If classical breaks AES-256: ML-KEM-768 still protects
```

---

## Sources

- [liboqs-python GitHub](https://github.com/open-quantum-safe/liboqs-python)
- [liboqs C Library](https://openquantumsafe.org/liboqs/)
- [pqcrypto PyPI](https://pypi.org/project/pqcrypto/)
- [SandboxAQ](https://www.sandboxaq.com/)
- [NIST PQC Standards](https://csrc.nist.gov/projects/post-quantum-cryptography)
- [NSA CNSA 2.0 Timeline](https://media.defense.gov/2022/Sep/07/2003071834/-1/-1/0/CSA_CNSA_2.0_ALGORITHMS_.PDF)
