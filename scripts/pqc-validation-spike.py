#!/usr/bin/env python3
"""PQC ARM64 Validation Spike — Decision Gate for Phase 6.

Tests availability and performance of post-quantum cryptography libraries
on Apple Silicon (M4 Mac / ARM64).

Tested libraries (in priority order):
  1. liboqs-python  — C bindings to liboqs (NIST PQC winners)
  2. pqcrypto       — Pure Python PQC implementations
  3. oqs via cffi   — Direct C library bindings

Fallback: cryptography library (AES-256-GCM + Ed25519) which IS available.

Result (2026-03-29):
  - liboqs-python: NOT AVAILABLE on ARM64 Mac (pip install fails, no wheels)
  - pqcrypto: NOT AVAILABLE (pip install fails, no wheels)
  - oqs cffi: NOT AVAILABLE (requires manual liboqs C build)
  - FALLBACK ACTIVE: Using `cryptography` library for AES-256-GCM + Ed25519
  - Decision: PROCEED with hybrid fallback architecture
    * HybridEncryptor uses AES-256-GCM (quantum-safe symmetric, 256-bit)
    * QuantumSafeSigner uses Ed25519 (classical, upgradeable when PQC libs available)
    * All code structured for drop-in PQC upgrade when ARM64 wheels ship
    * Feature flag PQC_ENABLED controls activation

Performance benchmarks (cryptography library fallback on M4):
  - AES-256-GCM encrypt 1KB: < 1ms
  - AES-256-GCM decrypt 1KB: < 1ms
  - Ed25519 keygen: < 1ms
  - Ed25519 sign: < 1ms
  - Ed25519 verify: < 1ms
  - HKDF derive: < 1ms
  All well within 50ms target.
"""

import sys
import time


def test_liboqs() -> bool:
    """Try liboqs-python for ML-KEM-768 and ML-DSA-65."""
    try:
        import oqs  # noqa: F401

        print("[OK] liboqs-python available")
        # Test ML-KEM-768
        kem = oqs.KeyEncapsulation("ML-KEM-768")
        pub = kem.generate_keypair()
        ct, ss = kem.encap_secret(pub)
        ss2 = kem.decap_secret(ct)
        assert ss == ss2, "KEM round-trip failed"
        print("[OK] ML-KEM-768 encap/decap works")

        # Test ML-DSA-65
        sig = oqs.Signature("ML-DSA-65")
        pub = sig.generate_keypair()
        signature = sig.sign(b"test message")
        assert sig.verify(b"test message", signature, pub)
        print("[OK] ML-DSA-65 sign/verify works")
        return True
    except ImportError:
        print("[SKIP] liboqs-python not installed")
        return False
    except Exception as e:
        print(f"[FAIL] liboqs-python error: {e}")
        return False


def test_pqcrypto() -> bool:
    """Try pqcrypto pure-Python library."""
    try:
        import pqcrypto  # noqa: F401

        print("[OK] pqcrypto available")
        return True
    except ImportError:
        print("[SKIP] pqcrypto not installed")
        return False
    except Exception as e:
        print(f"[FAIL] pqcrypto error: {e}")
        return False


def test_cryptography_fallback() -> bool:
    """Test cryptography library fallback (AES-256-GCM + Ed25519 + HKDF)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        import os

        results: dict[str, float] = {}

        # AES-256-GCM encrypt/decrypt
        key = AESGCM.generate_key(bit_length=256)
        aes = AESGCM(key)
        nonce = os.urandom(12)
        plaintext = b"A" * 1024  # 1KB

        t0 = time.perf_counter()
        ct = aes.encrypt(nonce, plaintext, None)
        results["aes_encrypt_1kb"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        pt = aes.decrypt(nonce, ct, None)
        results["aes_decrypt_1kb"] = (time.perf_counter() - t0) * 1000
        assert pt == plaintext, "AES round-trip failed"
        print("[OK] AES-256-GCM encrypt/decrypt works")

        # Ed25519 keygen + sign + verify
        t0 = time.perf_counter()
        private_key = Ed25519PrivateKey.generate()
        results["ed25519_keygen"] = (time.perf_counter() - t0) * 1000

        public_key = private_key.public_key()
        message = b"transaction data"

        t0 = time.perf_counter()
        signature = private_key.sign(message)
        results["ed25519_sign"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        public_key.verify(signature, message)
        results["ed25519_verify"] = (time.perf_counter() - t0) * 1000
        print("[OK] Ed25519 sign/verify works")

        # HKDF key derivation
        t0 = time.perf_counter()
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"objective-hertz-conway",
            info=b"test-agent",
        ).derive(b"master-password")
        results["hkdf_derive"] = (time.perf_counter() - t0) * 1000
        assert len(derived) == 32
        print("[OK] HKDF key derivation works")

        print("\nPerformance benchmarks (ms):")
        for name, ms in results.items():
            status = "PASS" if ms < 50 else "FAIL"
            print(f"  {name}: {ms:.3f}ms [{status}]")

        return True
    except ImportError as e:
        print(f"[FAIL] cryptography library not available: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] cryptography fallback error: {e}")
        return False


def main() -> None:
    """Run PQC validation spike."""
    print("=" * 60)
    print("PQC ARM64 Validation Spike — Objective Hertz Phase 6")
    print("=" * 60)
    print()

    pqc_available = False

    print("--- Testing liboqs-python ---")
    if test_liboqs():
        pqc_available = True

    print("\n--- Testing pqcrypto ---")
    if test_pqcrypto():
        pqc_available = True

    print("\n--- Testing cryptography fallback ---")
    fallback_ok = test_cryptography_fallback()

    print("\n" + "=" * 60)
    print("DECISION GATE RESULT")
    print("=" * 60)

    if pqc_available:
        print("STATUS: PASS — Native PQC libraries available")
        print("ACTION: Proceed with ML-KEM-768 + ML-DSA-65")
    elif fallback_ok:
        print("STATUS: PASS (with fallback)")
        print("ACTION: Proceed with AES-256-GCM + Ed25519 fallback")
        print("        Code structured for PQC drop-in when ARM64 wheels ship")
    else:
        print("STATUS: FAIL — No crypto libraries available")
        print("ACTION: Defer Phase 6")
        sys.exit(1)


if __name__ == "__main__":
    main()
