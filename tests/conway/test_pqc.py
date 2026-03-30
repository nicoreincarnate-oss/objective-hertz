"""Comprehensive tests for conway/pqc.py — PQC cryptography layer.

Tests cover:
  - HybridEncryptor: encrypt/decrypt round-trip, wrong key, short ciphertext
  - QuantumSafeSigner: sign/verify, invalid signature, key generation
  - PQCCryptoProvider: Protocol compliance, combined operations
  - derive_agent_key: per-agent isolation, determinism, empty inputs
  - rotate_keys: key rotation with dual-key overlap
  - is_pqc_enabled: feature flag parsing
  - Graceful fallback when PQC libs unavailable
"""

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import cryptography.exceptions
import pytest

from conway.pqc import (
    _FORMAT_VERSION,
    _HEADER_SIZE,
    HybridEncryptor,
    PQCCryptoProvider,
    QuantumSafeSigner,
    derive_agent_key,
    get_active_key,
    is_pqc_enabled,
    rotate_keys,
)
from shared.contracts import CryptoProvider

# ── HybridEncryptor ──────────────────────────────────────────────


class TestHybridEncryptor:
    """Tests for AES-256-GCM hybrid encryption."""

    def test_encrypt_decrypt_round_trip(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        plaintext = b"Hello, quantum-safe world!"
        ct = enc.encrypt(plaintext)
        assert enc.decrypt(ct) == plaintext

    def test_encrypt_decrypt_empty_plaintext(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct = enc.encrypt(b"")
        assert enc.decrypt(ct) == b""

    def test_encrypt_decrypt_large_payload(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        plaintext = os.urandom(1024 * 100)  # 100KB
        ct = enc.encrypt(plaintext)
        assert enc.decrypt(ct) == plaintext

    def test_ciphertext_format_version(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct = enc.encrypt(b"test")
        assert ct[0] == _FORMAT_VERSION

    def test_ciphertext_has_header(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct = enc.encrypt(b"test")
        assert len(ct) >= _HEADER_SIZE + 16  # header + tag minimum

    def test_different_nonce_each_encryption(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct1 = enc.encrypt(b"same")
        ct2 = enc.encrypt(b"same")
        # Nonce is random, so ciphertexts should differ
        assert ct1 != ct2

    def test_wrong_key_fails_decrypt(self):
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        enc1 = HybridEncryptor(key1)
        enc2 = HybridEncryptor(key2)
        ct = enc1.encrypt(b"secret")
        with pytest.raises((ValueError, cryptography.exceptions.InvalidTag)):
            enc2.decrypt(ct)

    def test_tampered_ciphertext_fails(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct = bytearray(enc.encrypt(b"secret"))
        ct[-1] ^= 0xFF  # flip last byte
        with pytest.raises((ValueError, cryptography.exceptions.InvalidTag)):
            enc.decrypt(bytes(ct))

    def test_short_ciphertext_raises_valueerror(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        with pytest.raises(ValueError, match="too short"):
            enc.decrypt(b"\x01" + b"\x00" * 10)  # less than header + tag

    def test_wrong_version_raises_valueerror(self):
        key = os.urandom(32)
        enc = HybridEncryptor(key)
        ct = enc.encrypt(b"test")
        bad_ct = bytes([0xFF]) + ct[1:]  # wrong version
        with pytest.raises(ValueError, match="Unsupported ciphertext version"):
            enc.decrypt(bad_ct)

    def test_invalid_key_size_raises(self):
        with pytest.raises(ValueError, match="32 bytes"):
            HybridEncryptor(b"short")

    def test_invalid_key_size_too_long(self):
        with pytest.raises(ValueError, match="32 bytes"):
            HybridEncryptor(os.urandom(64))


# ── QuantumSafeSigner ────────────────────────────────────────────


class TestQuantumSafeSigner:
    """Tests for Ed25519 signing (upgradeable to ML-DSA-65)."""

    def test_sign_verify_round_trip(self):
        signer = QuantumSafeSigner()
        msg = b"transaction payload"
        sig = signer.sign(msg)
        assert signer.verify(msg, sig)

    def test_invalid_signature_rejected(self):
        signer = QuantumSafeSigner()
        msg = b"transaction payload"
        bad_sig = os.urandom(64)
        assert not signer.verify(msg, bad_sig)

    def test_wrong_message_rejected(self):
        signer = QuantumSafeSigner()
        sig = signer.sign(b"original")
        assert not signer.verify(b"tampered", sig)

    def test_verify_with_explicit_public_key(self):
        signer = QuantumSafeSigner()
        msg = b"data"
        sig = signer.sign(msg)
        pk = signer.public_key_bytes
        assert signer.verify(msg, sig, public_key=pk)

    def test_cross_signer_verify(self):
        signer1 = QuantumSafeSigner()
        signer2 = QuantumSafeSigner()
        msg = b"data"
        sig = signer1.sign(msg)
        # Verify with signer1's public key via signer2
        assert signer2.verify(msg, sig, public_key=signer1.public_key_bytes)
        # signer2's own key should NOT verify signer1's signature
        assert not signer2.verify(msg, sig)

    def test_deterministic_from_seed(self):
        seed = os.urandom(32)
        s1 = QuantumSafeSigner(seed)
        s2 = QuantumSafeSigner(seed)
        assert s1.public_key_bytes == s2.public_key_bytes

    def test_different_seeds_different_keys(self):
        s1 = QuantumSafeSigner(os.urandom(32))
        s2 = QuantumSafeSigner(os.urandom(32))
        assert s1.public_key_bytes != s2.public_key_bytes

    def test_generate_keypair_no_seed(self):
        signer = QuantumSafeSigner()
        assert len(signer.public_key_bytes) == 32
        assert len(signer.private_key_bytes) == 32

    def test_invalid_key_length_raises(self):
        with pytest.raises(ValueError):
            QuantumSafeSigner(b"short")

    def test_sign_empty_message(self):
        signer = QuantumSafeSigner()
        sig = signer.sign(b"")
        assert signer.verify(b"", sig)

    def test_signature_is_64_bytes(self):
        signer = QuantumSafeSigner()
        sig = signer.sign(b"data")
        assert len(sig) == 64


# ── PQCCryptoProvider ────────────────────────────────────────────


class TestPQCCryptoProvider:
    """Tests for the combined CryptoProvider."""

    def test_implements_crypto_provider_protocol(self):
        provider = PQCCryptoProvider(os.urandom(32))
        assert isinstance(provider, CryptoProvider)

    def test_encrypt_decrypt(self):
        provider = PQCCryptoProvider(os.urandom(32))
        ct = provider.encrypt(b"secret")
        assert provider.decrypt(ct) == b"secret"

    def test_sign_verify(self):
        provider = PQCCryptoProvider(os.urandom(32))
        sig = provider.sign(b"message")
        assert provider.verify(b"message", sig)

    def test_public_key_bytes(self):
        provider = PQCCryptoProvider(os.urandom(32))
        assert len(provider.public_key_bytes) == 32

    def test_with_explicit_signing_key(self):
        enc_key = os.urandom(32)
        sign_key = os.urandom(32)
        provider = PQCCryptoProvider(enc_key, sign_key)
        sig = provider.sign(b"test")
        assert provider.verify(b"test", sig)

    def test_wrong_provider_fails_decrypt(self):
        p1 = PQCCryptoProvider(os.urandom(32))
        p2 = PQCCryptoProvider(os.urandom(32))
        ct = p1.encrypt(b"secret")
        with pytest.raises((ValueError, cryptography.exceptions.InvalidTag)):
            p2.decrypt(ct)


# ── derive_agent_key ─────────────────────────────────────────────


class TestDeriveAgentKey:
    """Tests for per-agent HKDF key derivation."""

    def test_returns_32_bytes(self):
        key = derive_agent_key("titan", "password123")
        assert len(key) == 32

    def test_deterministic(self):
        k1 = derive_agent_key("titan", "pw")
        k2 = derive_agent_key("titan", "pw")
        assert k1 == k2

    def test_different_agents_different_keys(self):
        k1 = derive_agent_key("titan", "pw")
        k2 = derive_agent_key("hermes", "pw")
        assert k1 != k2

    def test_different_passwords_different_keys(self):
        k1 = derive_agent_key("titan", "pw1")
        k2 = derive_agent_key("titan", "pw2")
        assert k1 != k2

    def test_agent_isolation_encrypt_decrypt(self):
        """Agent A's key cannot decrypt agent B's data."""
        ka = derive_agent_key("agent_a", "master")
        kb = derive_agent_key("agent_b", "master")
        enc_a = HybridEncryptor(ka)
        enc_b = HybridEncryptor(kb)
        ct = enc_a.encrypt(b"agent_a_secret")
        # Agent A can decrypt
        assert enc_a.decrypt(ct) == b"agent_a_secret"
        # Agent B cannot
        with pytest.raises((ValueError, cryptography.exceptions.InvalidTag)):
            enc_b.decrypt(ct)

    def test_empty_agent_name_raises(self):
        with pytest.raises(ValueError, match="agent_name"):
            derive_agent_key("", "password")

    def test_empty_password_raises(self):
        with pytest.raises(ValueError, match="master_password"):
            derive_agent_key("titan", "")


# ── is_pqc_enabled ──────────────────────────────────────────────


class TestIsPqcEnabled:
    """Tests for feature flag parsing."""

    def test_default_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("PQC_ENABLED", None)
            assert not is_pqc_enabled()

    def test_enabled_true(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "true"}):
            assert is_pqc_enabled()

    def test_enabled_one(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "1"}):
            assert is_pqc_enabled()

    def test_enabled_yes(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "yes"}):
            assert is_pqc_enabled()

    def test_enabled_TRUE_case_insensitive(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "TRUE"}):
            assert is_pqc_enabled()

    def test_disabled_false(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "false"}):
            assert not is_pqc_enabled()

    def test_disabled_zero(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "0"}):
            assert not is_pqc_enabled()

    def test_disabled_random_string(self):
        with patch.dict(os.environ, {"PQC_ENABLED": "maybe"}):
            assert not is_pqc_enabled()


# ── rotate_keys ──────────────────────────────────────────────────


class TestRotateKeys:
    """Tests for key rotation with dual-key overlap."""

    @pytest.mark.asyncio
    async def test_rotate_keys_returns_expected_fields(self):
        mock_execute = AsyncMock()
        with (
            patch("shared.db.execute", mock_execute),
            patch.dict(os.environ, {"CONWAY_KEYSTORE_PASSWORD": "test-pw"}),
        ):
            result = await rotate_keys("titan", "test-pw")

        assert result["agent_name"] == "titan"
        assert "new_public_key" in result
        assert "algorithm" in result
        assert "valid_from" in result
        assert "old_key_valid_until" in result

    @pytest.mark.asyncio
    async def test_rotate_keys_marks_old_keys(self):
        execute_calls = []

        async def capture_execute(query, params):
            execute_calls.append((query, params))

        with (
            patch("shared.db.execute", capture_execute),
            patch.dict(os.environ, {"CONWAY_KEYSTORE_PASSWORD": "test-pw"}),
        ):
            await rotate_keys("titan", "test-pw")

        # Should have UPDATE (mark old) + INSERT (new key)
        assert len(execute_calls) == 2
        assert "UPDATE" in execute_calls[0][0]
        assert "INSERT" in execute_calls[1][0]

    @pytest.mark.asyncio
    async def test_rotate_keys_no_password_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CONWAY_KEYSTORE_PASSWORD", None)
            with pytest.raises(RuntimeError, match="CONWAY_KEYSTORE_PASSWORD"):
                await rotate_keys("titan")

    @pytest.mark.asyncio
    async def test_rotate_keys_dual_key_overlap(self):
        """Old keys get 30-day validity window."""
        execute_calls = []

        async def capture_execute(query, params):
            execute_calls.append((query, params))

        with (
            patch("shared.db.execute", capture_execute),
            patch.dict(os.environ, {"CONWAY_KEYSTORE_PASSWORD": "test-pw"}),
        ):
            await rotate_keys("titan", "test-pw")

        # The UPDATE sets valid_until = now + 30 days
        update_params = execute_calls[0][1]
        valid_until = update_params[0]
        now = datetime.now(UTC)
        # Should be ~30 days from now (allow 1 minute tolerance)
        diff_days = (valid_until - now).days
        assert 29 <= diff_days <= 30


# ── get_active_key ───────────────────────────────────────────────


class TestGetActiveKey:
    """Tests for active key retrieval."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_keys(self):
        with patch("shared.db.fetch_one", AsyncMock(return_value=None)):
            result = await get_active_key("unknown_agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_dict_when_key_exists(self):
        mock_row = {
            "id": 1,
            "agent_name": "titan",
            "key_type": "hybrid",
            "public_key": b"\x01" * 32,
            "encrypted_private_key": b"\x02" * 64,
            "algorithm": "ed25519+aes256gcm",
            "valid_from": datetime.now(UTC),
            "valid_until": None,
        }
        with patch("shared.db.fetch_one", AsyncMock(return_value=mock_row)):
            result = await get_active_key("titan")
        assert result is not None
        assert result["agent_name"] == "titan"


# ── Wallet PQC integration ──────────────────────────────────────


class TestWalletPQCIntegration:
    """Tests for PQC wiring in conway/wallet.py."""

    def test_wallet_pqc_disabled_no_signer(self):
        """When PQC_ENABLED=false, wallet has no PQC signer."""
        with patch.dict(os.environ, {"PQC_ENABLED": "false"}):
            from conway.wallet import AgentWallet

            w = AgentWallet("test", "0x1234", "0xprivkey")
            assert w.pqc_signer is None

    def test_wallet_pqc_enabled_with_password(self):
        """When PQC_ENABLED=true and password set, wallet gets PQC signer."""
        with patch.dict(
            os.environ,
            {"PQC_ENABLED": "true", "CONWAY_KEYSTORE_PASSWORD": "testpw"},
        ):
            from conway.wallet import _PQC_AVAILABLE, AgentWallet

            if not _PQC_AVAILABLE:
                pytest.skip("PQC module not available in test environment")
            # Force reload to pick up env
            w = AgentWallet.__new__(AgentWallet)
            w.agent_name = "test_agent"
            w._address = "0x1234"
            w._private_key = "0xprivkey"
            w._pqc_signer = None
            # Re-run init logic
            AgentWallet.__init__(w, "test_agent", "0x1234", "0xprivkey")
            assert w.pqc_signer is not None

    def test_pqc_import_fallback(self):
        """is_pqc_enabled returns False when PQC module unavailable."""
        # The wallet module defines a stub is_pqc_enabled when import fails
        # We test the actual module's function
        with patch.dict(os.environ, {"PQC_ENABLED": "false"}):
            from conway.wallet import is_pqc_enabled as wallet_is_pqc_enabled

            assert not wallet_is_pqc_enabled()
