"""
Regression tests for FIX-02: Conway keystore password security.

Proves that keystore encryption uses the CONWAY_KEYSTORE_PASSWORD env var,
never the agent name, and that missing/empty password raises RuntimeError.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _get_keystore_password tests ──────────────────────────────────


def test_get_keystore_password_raises_when_unset(monkeypatch):
    """Missing CONWAY_KEYSTORE_PASSWORD raises RuntimeError."""
    monkeypatch.delenv("CONWAY_KEYSTORE_PASSWORD", raising=False)

    from conway.wallet import _get_keystore_password

    with pytest.raises(RuntimeError, match="CONWAY_KEYSTORE_PASSWORD"):
        _get_keystore_password()


def test_get_keystore_password_raises_when_empty(monkeypatch):
    """Empty CONWAY_KEYSTORE_PASSWORD raises RuntimeError."""
    monkeypatch.setenv("CONWAY_KEYSTORE_PASSWORD", "")

    from conway.wallet import _get_keystore_password

    with pytest.raises(RuntimeError, match="CONWAY_KEYSTORE_PASSWORD"):
        _get_keystore_password()


def test_get_keystore_password_returns_value(monkeypatch):
    """Valid CONWAY_KEYSTORE_PASSWORD is returned as-is."""
    monkeypatch.setenv("CONWAY_KEYSTORE_PASSWORD", "strong-secret-123")

    from conway.wallet import _get_keystore_password

    assert _get_keystore_password() == "strong-secret-123"


# ── Encrypt/Decrypt password usage tests ──────────────────────────


def test_encrypt_uses_env_password(tmp_path, monkeypatch):
    """_create_new_wallet encrypts keystore with env password, not agent name."""
    monkeypatch.setenv("CONWAY_KEYSTORE_PASSWORD", "test-pw-456")

    fake_acct = MagicMock()
    fake_acct.address = "0x" + "a" * 40
    fake_acct.key.hex.return_value = "0x" + "b" * 64

    mock_account = MagicMock()
    mock_account.create.return_value = fake_acct
    mock_account.encrypt.return_value = {"version": 3}

    with patch.dict("sys.modules", {"eth_account": MagicMock(Account=mock_account)}):
        from conway.wallet import WalletManager

        mgr = WalletManager(keystore_dir=str(tmp_path))
        mgr._create_new_wallet("hermes")

    # Encrypt must use env password, NOT agent name "hermes"
    mock_account.encrypt.assert_called_once()
    call_args = mock_account.encrypt.call_args
    assert call_args[0][1] == "test-pw-456"
    assert call_args[0][1] != "hermes"


def test_decrypt_uses_env_password(tmp_path, monkeypatch):
    """_load_from_keystore decrypts keystore with env password, not agent name."""
    monkeypatch.setenv("CONWAY_KEYSTORE_PASSWORD", "test-pw-789")

    # Write a fake keystore file
    keystore_data = {"version": 3, "crypto": {}}
    keystore_path = tmp_path / "hermes.json"
    keystore_path.write_text(json.dumps(keystore_data))

    mock_account = MagicMock()
    mock_account.decrypt.return_value = bytes.fromhex("bb" * 32)

    with patch.dict("sys.modules", {"eth_account": MagicMock(Account=mock_account)}):
        from conway.wallet import WalletManager

        mgr = WalletManager(keystore_dir=str(tmp_path))
        mgr._load_from_keystore("hermes", "0x" + "aa" * 20, "hermes.json")

    # Decrypt must use env password, NOT agent name "hermes"
    mock_account.decrypt.assert_called_once()
    call_args = mock_account.decrypt.call_args
    assert call_args[0][1] == "test-pw-789"
    assert call_args[0][1] != "hermes"
