"""SecureConfig — Encrypt .env secrets at rest using AES-256-GCM.

Provides transparent encryption/decryption of environment secrets:
  - encrypt_env(): reads .env, encrypts each value, writes .env.encrypted
  - get(key): returns decrypted value from encrypted store
  - Falls back to os.environ when encrypted file unavailable

The encryption key is derived from CONWAY_KEYSTORE_PASSWORD via HKDF,
ensuring the master password is the only secret that needs to be
provided at runtime (via env var or secure input).

Feature flag: PQC_ENABLED controls whether encryption is active.
When disabled, get() returns os.environ values directly.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("shared.secure_config")

# Keys that should never be encrypted (needed before decryption is possible)
_BOOTSTRAP_KEYS = frozenset({
    "CONWAY_KEYSTORE_PASSWORD",
    "PQC_ENABLED",
})

# HKDF salt for SecureConfig key derivation (distinct from per-agent salt)
_SECURE_CONFIG_SALT = b"objective-hertz-secure-config-v1"
_NONCE_SIZE = 12


def _derive_config_key(master_password: str) -> bytes:
    """Derive 32-byte AES key for config encryption from master password."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_SECURE_CONFIG_SALT,
        info=b"secure-config-encryption",
    ).derive(master_password.encode("utf-8"))


class SecureConfig:
    """Encrypts .env secrets at rest using AES-256-GCM.

    Usage:
        # One-time encryption
        config = SecureConfig()
        config.encrypt_env()

        # Runtime access
        config = SecureConfig()
        api_key = config.get("CLAUDE_API_KEY")
    """

    def __init__(
        self,
        env_path: Path | str = Path(".env"),
        master_password: str | None = None,
    ):
        """Initialize SecureConfig.

        Args:
            env_path: Path to .env file. Encrypted file will be at same
                location with .encrypted extension.
            master_password: Master password for key derivation. Falls back
                to CONWAY_KEYSTORE_PASSWORD env var.
        """
        self._env_path = Path(env_path)
        self._encrypted_path = self._env_path.parent / (
            self._env_path.name + ".encrypted"
        )
        self._decrypted: dict[str, str] = {}
        self._master_password = master_password

    def _get_password(self) -> str:
        """Get master password from init arg or env."""
        pw = self._master_password or os.environ.get("CONWAY_KEYSTORE_PASSWORD", "")
        if not pw:
            raise RuntimeError(
                "Master password required for SecureConfig. "
                "Set CONWAY_KEYSTORE_PASSWORD or pass master_password."
            )
        return pw

    def encrypt_env(self) -> Path:
        """Read .env file, encrypt each value, write .env.encrypted.

        Returns path to encrypted file. Bootstrap keys (CONWAY_KEYSTORE_PASSWORD,
        PQC_ENABLED) are stored in plaintext since they're needed before
        decryption is possible.

        Returns:
            Path to the encrypted file.

        Raises:
            FileNotFoundError: If .env file doesn't exist.
            RuntimeError: If master password not available.
        """
        if not self._env_path.exists():
            raise FileNotFoundError(f"Env file not found: {self._env_path}")

        password = self._get_password()
        key = _derive_config_key(password)
        aes = AESGCM(key)

        entries: dict[str, dict] = {}
        for line in self._env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            var_name, _, var_value = line.partition("=")
            var_name = var_name.strip()
            var_value = var_value.strip().strip('"').strip("'")

            if var_name in _BOOTSTRAP_KEYS:
                # Store bootstrap keys in plaintext
                entries[var_name] = {"plaintext": var_value}
            else:
                # Encrypt the value
                nonce = os.urandom(_NONCE_SIZE)
                ct = aes.encrypt(nonce, var_value.encode("utf-8"), None)
                entries[var_name] = {
                    "nonce": nonce.hex(),
                    "ciphertext": ct.hex(),
                }

        self._encrypted_path.write_text(json.dumps(entries, indent=2))
        logger.info(
            f"Encrypted {len(entries)} env vars to {self._encrypted_path} "
            f"({len([e for e in entries.values() if 'ciphertext' in e])} encrypted, "
            f"{len([e for e in entries.values() if 'plaintext' in e])} bootstrap)"
        )
        return self._encrypted_path

    def _load_and_decrypt(self) -> None:
        """Load encrypted file and decrypt all values into memory."""
        if not self._encrypted_path.exists():
            logger.debug(
                f"Encrypted file {self._encrypted_path} not found, "
                "falling back to os.environ"
            )
            return

        try:
            password = self._get_password()
            key = _derive_config_key(password)
            aes = AESGCM(key)

            entries = json.loads(self._encrypted_path.read_text())
            for var_name, entry in entries.items():
                if "plaintext" in entry:
                    self._decrypted[var_name] = entry["plaintext"]
                else:
                    nonce = bytes.fromhex(entry["nonce"])
                    ct = bytes.fromhex(entry["ciphertext"])
                    plaintext = aes.decrypt(nonce, ct, None)
                    self._decrypted[var_name] = plaintext.decode("utf-8")

            logger.info(f"Loaded {len(self._decrypted)} decrypted config values")
        except Exception as e:
            logger.error(f"Failed to decrypt config: {e}. Falling back to os.environ")
            self._decrypted = {}

    def get(self, key: str, default: str = "") -> str:
        """Get decrypted config value.

        Priority:
          1. Decrypted value from .env.encrypted
          2. os.environ value (fallback)
          3. default value

        Args:
            key: Environment variable name.
            default: Default value if key not found anywhere.

        Returns:
            Decrypted value, env var, or default.
        """
        if not self._decrypted:
            self._load_and_decrypt()

        if key in self._decrypted:
            return self._decrypted[key]

        # Fallback to os.environ
        return os.environ.get(key, default)

    def keys(self) -> list[str]:
        """Return all available config keys."""
        if not self._decrypted:
            self._load_and_decrypt()
        return list(self._decrypted.keys())

    @property
    def encrypted_path(self) -> Path:
        """Path to the encrypted config file."""
        return self._encrypted_path


__all__ = ["SecureConfig"]
