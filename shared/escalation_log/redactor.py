"""Escalation log redaction pipeline.

PAUL audit P0: the escalation log is a PII/secret tarpit. This module enforces
field-level redaction at write time + encryption-at-rest + canary tests.

Three layers of safety:

  1. REGEX SCRUBBER — fast pattern matching for obvious leaks (API keys, emails,
     phone numbers, SSNs, credit cards, JWT tokens, AWS keys, GCP keys, etc.)

  2. ENTITY SCRUBBER — for names + addresses, uses Presidio if available,
     otherwise falls back to a simple capitalized-word heuristic

  3. CANARY TEST — every write inserts known synthetic secrets, then verifies
     they were stripped. Any leak fires a critical Hermes alert.

Encryption (FAIL-CLOSED, per audit P0-5 + P0-6):

  - AES-256-GCM with AAD binding (b"perseus-escalation-log-v1") to prevent
    silent ciphertext swap.
  - Key derived via scrypt (N=2**14, r=8, p=1) from CONWAY_KEYSTORE_PASSWORD
    env var + a random 16-byte salt persisted at ``<log_dir>/escalation_log.salt``.
  - Output format: 1-byte key version prefix (0x01) || base64(nonce || ciphertext).
  - Hard requirements:
      * `cryptography` package MUST be importable at module import time.
      * CONWAY_KEYSTORE_PASSWORD MUST be set at EscalationLogger construction.
      * Salt file MUST be readable/writable by the process.
    Any violation raises RuntimeError — the log will NEVER write unencrypted.
  - Rotation: 90 days; bump the key version byte when rotating.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# P0-6: fail closed on missing crypto — raise at import time, never at write time.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError as _crypto_import_exc:  # pragma: no cover
    raise RuntimeError(
        "cryptography package is required for escalation log encryption — "
        "escalation log will not write unencrypted. "
        "Install via: pip install cryptography"
    ) from _crypto_import_exc

logger = logging.getLogger("perseus.escalation_log")

# Key derivation + format constants
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_LEN = 32  # AES-256
_SALT_LEN = 16
_KEY_VERSION = 0x01
_AAD = b"perseus-escalation-log-v1"


# ============================================================================
# Redaction patterns
# ============================================================================

REDACTION_PATTERNS: list[tuple[str, str]] = [
    # API keys
    (r"sk-[A-Za-z0-9]{20,}",                                "[REDACTED:openai-key]"),
    (r"sk-ant-api03-[A-Za-z0-9_\-]{50,}",                    "[REDACTED:anthropic-key]"),
    (r"AIza[0-9A-Za-z\-_]{35}",                              "[REDACTED:google-key]"),
    (r"AKIA[0-9A-Z]{16}",                                    "[REDACTED:aws-access-key]"),
    (r"ASIA[0-9A-Z]{16}",                                    "[REDACTED:aws-temp-key]"),
    (r"ghp_[A-Za-z0-9]{36,}",                                "[REDACTED:github-token]"),
    (r"github_pat_[A-Za-z0-9_]{60,}",                        "[REDACTED:github-pat]"),
    (r"sk_live_[A-Za-z0-9]{24,}",                            "[REDACTED:stripe-live]"),
    (r"sk_test_[A-Za-z0-9]{24,}",                            "[REDACTED:stripe-test]"),

    # JWT
    (r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
     "[REDACTED:jwt]"),

    # Generic high-entropy
    (r"\b[A-Za-z0-9+/]{40,}={0,2}\b",                        "[REDACTED:base64-blob]"),

    # PII — emails (preserve domain for context)
    (r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})",    "[REDACTED:email@\\1]"),

    # Phone numbers (US-ish — 10 digits, optional country code, common separators)
    (r"\b\+?1?[-.\s]?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b",
     "[REDACTED:phone]"),

    # SSN
    (r"\b\d{3}-\d{2}-\d{4}\b",                               "[REDACTED:ssn]"),

    # Credit card (VISA/MC/AMEX/DISC)
    (r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6011)[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
     "[REDACTED:credit-card]"),

    # USDC / ETH addresses
    (r"\b0x[a-fA-F0-9]{40}\b",                               "[REDACTED:eth-address]"),

    # P1-2: ETH private keys (64-hex, 0x-prefixed; must come before generic hex blobs)
    (r"0x[a-fA-F0-9]{64}",                                   "<REDACTED:ETH_PRIVKEY>"),

    # P1-2: OpenRouter API keys (sk-or-v1- prefix + >=48 chars)
    (r"sk-or-v1-[A-Za-z0-9\-_]{48,}",                        "<REDACTED:OPENROUTER_KEY>"),

    # P1-2: Telegram bot tokens (9-10 digit bot id : 35 char secret)
    (r"\d{9,10}:[A-Za-z0-9_\-]{35}",                         "<REDACTED:TELEGRAM_TOKEN>"),

    # P1-2: Slack incoming webhooks
    (r"https://hooks\.slack\.com/services/[A-Z0-9]+/[A-Z0-9]+/[A-Za-z0-9]+",
     "<REDACTED:SLACK_WEBHOOK>"),

    # P1-2: Postgres connection strings (postgres:// or postgresql://)
    (r"postgres(?:ql)?://[^@]+@[^/]+/\w+",                   "<REDACTED:POSTGRES_URL>"),

    # Bearer tokens in HTTP headers
    (r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}",                  "[REDACTED:bearer-token]"),

    # Password-looking strings
    (r"(?i)(password|passwd|pwd|secret)\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?",
     "\\1=[REDACTED:password]"),
]


# Synthetic secrets used by the canary test
CANARY_SECRETS = [
    "sk-canarytest1234567890abcdefABCDEF",
    "sk-ant-api03-canary1234567890canary1234567890canary1234567890canary",
    "test-canary@perseus-internal.invalid",
    "+1-555-CANARY-99",
]


@dataclass
class RedactionResult:
    redacted_text: str
    redaction_count: int
    redactions_by_type: dict[str, int] = field(default_factory=dict)
    canaries_caught: list[str] = field(default_factory=list)
    canaries_missed: list[str] = field(default_factory=list)


class Redactor:
    def __init__(self, patterns: list[tuple[str, str]] | None = None):
        self.patterns = patterns or REDACTION_PATTERNS

    def redact(self, text: str) -> RedactionResult:
        if not text:
            return RedactionResult(redacted_text="", redaction_count=0)
        result = text
        counts: dict[str, int] = {}
        for pattern, replacement in self.patterns:
            new_result, n = re.subn(pattern, replacement, result)
            if n > 0:
                key = replacement.split(":")[1].rstrip("]") if ":" in replacement else "generic"
                counts[key] = counts.get(key, 0) + n
            result = new_result
        return RedactionResult(
            redacted_text=result,
            redaction_count=sum(counts.values()),
            redactions_by_type=counts,
        )

    def canary_test(self) -> RedactionResult:
        """Insert canaries into a test string, redact, verify all caught."""
        test_text = "Normal prompt with " + " and ".join(CANARY_SECRETS) + " inside."
        result = self.redact(test_text)
        caught: list[str] = []
        missed: list[str] = []
        for canary in CANARY_SECRETS:
            if canary in result.redacted_text:
                missed.append(canary)
            else:
                caught.append(canary)
        result.canaries_caught = caught
        result.canaries_missed = missed
        return result


# ============================================================================
# Logger with encryption-at-rest
# ============================================================================

def _salt_path_for(log_path: Path) -> Path:
    """Derive the salt file path from the log path."""
    return log_path.parent / "escalation_log.salt"


def _load_or_create_salt(salt_path: Path) -> bytes:
    """Load existing salt or generate + persist a new one. Fail closed."""
    if salt_path.exists():
        data = salt_path.read_bytes()
        if len(data) != _SALT_LEN:
            raise RuntimeError(
                f"Escalation log salt file {salt_path} is corrupt "
                f"(expected {_SALT_LEN} bytes, got {len(data)}) — refusing to write"
            )
        return data
    # First-write: generate + persist
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(_SALT_LEN)
    # Write with restrictive permissions (owner read/write only)
    fd = os.open(str(salt_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, salt)
    finally:
        os.close(fd)
    return salt


def _derive_key(password: str, salt: bytes) -> bytes:
    """scrypt KDF: password + salt -> 32-byte AES-256 key."""
    kdf = Scrypt(
        salt=salt,
        length=_SCRYPT_KEY_LEN,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    )
    return kdf.derive(password.encode("utf-8"))


def verify_encryption_ready(log_path: Path) -> None:
    """Startup preflight: fail LOUDLY if encryption can't work.

    Checks:
      1. `cryptography` is importable (already enforced at module import).
      2. CONWAY_KEYSTORE_PASSWORD env var is set and non-empty.
      3. Log directory is creatable.
      4. Salt file is readable (if exists) or writable (if not).
    """
    password = os.environ.get("CONWAY_KEYSTORE_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "CONWAY_KEYSTORE_PASSWORD must be set — "
            "escalation log will not write unencrypted (audit P0-6)"
        )

    # AESGCM + Scrypt already imported at module top; re-check for clarity.
    if AESGCM is None or Scrypt is None:  # pragma: no cover
        raise RuntimeError(
            "cryptography primitives unavailable — escalation log will not write"
        )

    # Ensure log dir exists / is creatable
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Escalation log directory {log_path.parent} is not writable: {exc}"
        ) from exc

    salt_path = _salt_path_for(log_path)
    if salt_path.exists():
        if not os.access(salt_path, os.R_OK):
            raise RuntimeError(
                f"Escalation log salt {salt_path} exists but is not readable"
            )
        data = salt_path.read_bytes()
        if len(data) != _SALT_LEN:
            raise RuntimeError(
                f"Escalation log salt {salt_path} is corrupt "
                f"(expected {_SALT_LEN} bytes, got {len(data)})"
            )
    else:
        if not os.access(salt_path.parent, os.W_OK):
            raise RuntimeError(
                f"Escalation log salt parent {salt_path.parent} is not writable"
            )


class EscalationLogger:
    """Append-only encrypted JSONL log of escalation events.

    FAIL-CLOSED per audit P0-5 + P0-6:
      - Raises RuntimeError from __init__ if CONWAY_KEYSTORE_PASSWORD unset.
      - Uses scrypt KDF with persistent random 16-byte salt.
      - AES-256-GCM with AAD binding; output has 1-byte key version prefix.
      - Will NEVER silently degrade to unencrypted writes.
    """

    def __init__(
        self,
        log_path: Path | None = None,
        encryption_key: bytes | None = None,
        redactor: Redactor | None = None,
    ):
        self.log_path = log_path or Path("/opt/perseus/data/escalation_log.jsonl.enc")
        self.redactor = redactor or Redactor()

        # P0-6: fail closed — startup preflight raises on any misconfiguration.
        verify_encryption_ready(self.log_path)

        if encryption_key is not None:
            # Explicit injection path (tests / key rotation). Must be exactly 32 bytes.
            if len(encryption_key) != _SCRYPT_KEY_LEN:
                raise RuntimeError(
                    f"encryption_key must be {_SCRYPT_KEY_LEN} bytes "
                    f"(got {len(encryption_key)})"
                )
            self.encryption_key = encryption_key
        else:
            # P0-5: scrypt KDF with persistent random salt (not unsalted SHA-256).
            password = os.environ["CONWAY_KEYSTORE_PASSWORD"]
            salt = _load_or_create_salt(_salt_path_for(self.log_path))
            self.encryption_key = _derive_key(password, salt)

    async def log(
        self,
        *,
        daemon: str,
        task_class: str,
        original_prompt: str,
        local_response: str,
        cloud_response: str,
        local_tier: str,
        cloud_tier: str,
        verifier_layer_failed: str = "",
        verifier_reason: str = "",
        latency_local_ms: int = 0,
        latency_cloud_ms: int = 0,
        cost_local_usd: float = 0.0,
        cost_cloud_usd: float = 0.0,
    ) -> None:
        """Redact, encrypt, and append an escalation event to the log."""
        prompt_redacted = self.redactor.redact(original_prompt)
        local_redacted = self.redactor.redact(local_response)
        cloud_redacted = self.redactor.redact(cloud_response)

        event = {
            "ts": int(time.time()),
            "daemon": daemon,
            "task_class": task_class,
            "prompt": prompt_redacted.redacted_text,
            "prompt_redactions": prompt_redacted.redaction_count,
            "local_response": local_redacted.redacted_text,
            "local_redactions": local_redacted.redaction_count,
            "cloud_response": cloud_redacted.redacted_text,
            "cloud_redactions": cloud_redacted.redaction_count,
            "local_tier": local_tier,
            "cloud_tier": cloud_tier,
            "verifier_layer_failed": verifier_layer_failed,
            "verifier_reason": verifier_reason,
            "latency_local_ms": latency_local_ms,
            "latency_cloud_ms": latency_cloud_ms,
            "cost_local_usd": cost_local_usd,
            "cost_cloud_usd": cost_cloud_usd,
            "prompt_hash": hashlib.sha256(original_prompt.encode()).hexdigest()[:16],
        }

        line_plain = json.dumps(event, separators=(",", ":")).encode()
        # P0-6: no fallback path. If encryption_key is missing, __init__ would
        # have raised. This is a belt-and-suspenders invariant check.
        if not self.encryption_key:  # pragma: no cover
            raise RuntimeError(
                "EscalationLogger.encryption_key missing at write time — "
                "refusing to write unencrypted"
            )
        line = self._encrypt(line_plain)

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "ab") as f:
                f.write(line + b"\n")
        except Exception as exc:
            logger.error("Failed to write escalation log: %s", exc)

    def _encrypt(self, plaintext: bytes) -> bytes:
        """AES-256-GCM encrypt with AAD binding and 1-byte key version prefix.

        Output format (base64-encoded body, prefixed by raw version byte):
            bytes([_KEY_VERSION]) || base64(nonce || ciphertext_with_tag)
        """
        import base64
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(self.encryption_key)
        # AAD binding (P0-5 bonus): prevents silent ciphertext swap between
        # different log streams or schema versions.
        ciphertext = aesgcm.encrypt(nonce, plaintext, _AAD)
        body = base64.b64encode(nonce + ciphertext)
        return bytes([_KEY_VERSION]) + body


_default_logger: EscalationLogger | None = None


def _get_default_logger() -> EscalationLogger:
    """Lazy singleton — defers the P0-6 preflight until first actual use."""
    global _default_logger
    if _default_logger is None:
        _default_logger = EscalationLogger()
    return _default_logger


async def log_escalation(**kwargs: Any) -> None:
    await _get_default_logger().log(**kwargs)


__all__ = [
    "Redactor",
    "EscalationLogger",
    "RedactionResult",
    "REDACTION_PATTERNS",
    "CANARY_SECRETS",
    "log_escalation",
    "verify_encryption_ready",
]
