"""Comprehensive tests for shared/secure_config.py — SecureConfig .env encryption.

Tests cover:
  - encrypt_env: reads .env, encrypts values, writes .env.encrypted
  - get: decrypts and returns values, falls back to os.environ
  - Bootstrap keys stored in plaintext (CONWAY_KEYSTORE_PASSWORD, PQC_ENABLED)
  - Missing .env file handling
  - Missing master password handling
  - Round-trip: encrypt then read back
  - keys() method
"""

import json
import os

import pytest

from shared.secure_config import SecureConfig


@pytest.fixture
def tmp_env(tmp_path):
    """Create a temporary .env file for testing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CLAUDE_API_KEY=sk-test-123456\n"
        "DATABASE_URL=postgres://user:pass@localhost/db\n"
        "CONWAY_KEYSTORE_PASSWORD=master-secret\n"
        "PQC_ENABLED=true\n"
        "# This is a comment\n"
        "\n"
        "EMPTY_LINE_ABOVE=value\n"
        'QUOTED_VALUE="hello world"\n'
    )
    return env_file


@pytest.fixture
def config_with_env(tmp_env):
    """SecureConfig with a valid .env and master password."""
    return SecureConfig(env_path=tmp_env, master_password="master-secret")


class TestEncryptEnv:
    """Tests for .env encryption."""

    def test_encrypt_creates_encrypted_file(self, config_with_env, tmp_env):
        result_path = config_with_env.encrypt_env()
        assert result_path.exists()
        assert result_path.name == ".env.encrypted"

    def test_encrypted_file_is_valid_json(self, config_with_env):
        config_with_env.encrypt_env()
        data = json.loads(config_with_env.encrypted_path.read_text())
        assert isinstance(data, dict)

    def test_bootstrap_keys_stored_plaintext(self, config_with_env):
        config_with_env.encrypt_env()
        data = json.loads(config_with_env.encrypted_path.read_text())
        # Bootstrap keys should have "plaintext" field
        assert "plaintext" in data["CONWAY_KEYSTORE_PASSWORD"]
        assert data["CONWAY_KEYSTORE_PASSWORD"]["plaintext"] == "master-secret"
        assert "plaintext" in data["PQC_ENABLED"]
        assert data["PQC_ENABLED"]["plaintext"] == "true"

    def test_secret_keys_encrypted(self, config_with_env):
        config_with_env.encrypt_env()
        data = json.loads(config_with_env.encrypted_path.read_text())
        # Non-bootstrap keys should have "ciphertext" and "nonce"
        assert "ciphertext" in data["CLAUDE_API_KEY"]
        assert "nonce" in data["CLAUDE_API_KEY"]
        # Should NOT have plaintext
        assert "plaintext" not in data["CLAUDE_API_KEY"]

    def test_comments_and_empty_lines_skipped(self, config_with_env):
        config_with_env.encrypt_env()
        data = json.loads(config_with_env.encrypted_path.read_text())
        # Should have 6 entries (not comments or empty lines)
        assert len(data) == 6

    def test_missing_env_raises_file_not_found(self, tmp_path):
        config = SecureConfig(
            env_path=tmp_path / "nonexistent.env",
            master_password="pw",
        )
        with pytest.raises(FileNotFoundError):
            config.encrypt_env()

    def test_missing_password_raises_runtime_error(self, tmp_env):
        with pytest.raises(RuntimeError, match="Master password"):
            config = SecureConfig(env_path=tmp_env, master_password="")
            config.encrypt_env()

    def test_quoted_values_unquoted(self, config_with_env):
        config_with_env.encrypt_env()
        # Read back the quoted value
        val = config_with_env.get("QUOTED_VALUE")
        assert val == "hello world"  # Quotes should be stripped


class TestGet:
    """Tests for config value retrieval."""

    def test_round_trip_encrypted_value(self, config_with_env):
        config_with_env.encrypt_env()
        # Create a new config instance that reads from encrypted file
        config2 = SecureConfig(
            env_path=config_with_env._env_path,
            master_password="master-secret",
        )
        assert config2.get("CLAUDE_API_KEY") == "sk-test-123456"

    def test_round_trip_all_values(self, config_with_env):
        config_with_env.encrypt_env()
        config2 = SecureConfig(
            env_path=config_with_env._env_path,
            master_password="master-secret",
        )
        assert config2.get("DATABASE_URL") == "postgres://user:pass@localhost/db"
        assert config2.get("CONWAY_KEYSTORE_PASSWORD") == "master-secret"
        assert config2.get("PQC_ENABLED") == "true"
        assert config2.get("EMPTY_LINE_ABOVE") == "value"

    def test_missing_key_returns_default(self, config_with_env):
        config_with_env.encrypt_env()
        config2 = SecureConfig(
            env_path=config_with_env._env_path,
            master_password="master-secret",
        )
        assert config2.get("NONEXISTENT", "fallback") == "fallback"

    def test_fallback_to_os_environ(self, tmp_path):
        """When no encrypted file exists, falls back to os.environ."""
        config = SecureConfig(
            env_path=tmp_path / "nonexistent.env",
            master_password="pw",
        )
        os.environ["TEST_SECURE_CONFIG_VAR"] = "from_env"
        try:
            assert config.get("TEST_SECURE_CONFIG_VAR") == "from_env"
        finally:
            del os.environ["TEST_SECURE_CONFIG_VAR"]

    def test_encrypted_value_takes_precedence_over_env(self, config_with_env):
        config_with_env.encrypt_env()
        os.environ["CLAUDE_API_KEY"] = "env-override"
        try:
            config2 = SecureConfig(
                env_path=config_with_env._env_path,
                master_password="master-secret",
            )
            # Encrypted value should win
            assert config2.get("CLAUDE_API_KEY") == "sk-test-123456"
        finally:
            del os.environ["CLAUDE_API_KEY"]

    def test_wrong_password_falls_back_to_environ(self, config_with_env):
        """Wrong password causes decrypt failure, falls back to os.environ."""
        config_with_env.encrypt_env()
        config_bad = SecureConfig(
            env_path=config_with_env._env_path,
            master_password="wrong-password",
        )
        os.environ["CLAUDE_API_KEY"] = "env-fallback"
        try:
            # Should not crash, falls back to environ
            val = config_bad.get("CLAUDE_API_KEY", "default")
            assert val in ("env-fallback", "default")
        finally:
            del os.environ["CLAUDE_API_KEY"]


class TestKeys:
    """Tests for listing available config keys."""

    def test_keys_lists_all_encrypted(self, config_with_env):
        config_with_env.encrypt_env()
        config2 = SecureConfig(
            env_path=config_with_env._env_path,
            master_password="master-secret",
        )
        keys = config2.keys()
        assert "CLAUDE_API_KEY" in keys
        assert "DATABASE_URL" in keys
        assert "CONWAY_KEYSTORE_PASSWORD" in keys
        assert "PQC_ENABLED" in keys

    def test_keys_empty_when_no_encrypted_file(self, tmp_path):
        config = SecureConfig(
            env_path=tmp_path / "nonexistent.env",
            master_password="pw",
        )
        assert config.keys() == []


class TestSecureConfigEdgeCases:
    """Edge case tests."""

    def test_env_with_equals_in_value(self, tmp_path):
        """Values containing '=' should be handled correctly."""
        env_file = tmp_path / ".env"
        env_file.write_text("CONNECTION=host=localhost;port=5432\n")
        config = SecureConfig(env_path=env_file, master_password="pw")
        config.encrypt_env()
        config2 = SecureConfig(env_path=env_file, master_password="pw")
        assert config2.get("CONNECTION") == "host=localhost;port=5432"

    def test_env_with_no_password_env_var(self, tmp_path):
        """Missing password from both init and env raises."""
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n")
        orig = os.environ.pop("CONWAY_KEYSTORE_PASSWORD", None)
        try:
            config = SecureConfig(env_path=env_file)
            with pytest.raises(RuntimeError):
                config.encrypt_env()
        finally:
            if orig is not None:
                os.environ["CONWAY_KEYSTORE_PASSWORD"] = orig

    def test_encrypted_path_property(self, tmp_path):
        config = SecureConfig(env_path=tmp_path / ".env", master_password="pw")
        assert config.encrypted_path == tmp_path / ".env.encrypted"

    def test_reencrypt_overwrites(self, config_with_env):
        """Re-encrypting overwrites the existing encrypted file."""
        config_with_env.encrypt_env()
        assert config_with_env.encrypted_path.exists()
        # Re-encrypt should succeed without error
        config_with_env.encrypt_env()
        assert config_with_env.encrypted_path.exists()
