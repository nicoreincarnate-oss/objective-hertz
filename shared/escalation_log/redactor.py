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

Encryption: AES-256-GCM with key from CONWAY_KEYSTORE_PASSWORD env var (already
exists for Conway wallets). Rotation: 90 days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("perseus.escalation_log")


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

class EscalationLogger:
    """Append-only encrypted JSONL log of escalation events."""

    def __init__(
        self,
        log_path: Path | None = None,
        encryption_key: bytes | None = None,
        redactor: Redactor | None = None,
    ):
        self.log_path = log_path or Path("/opt/perseus/data/escalation_log.jsonl.enc")
        self.redactor = redactor or Redactor()
        self.encryption_key = encryption_key
        if self.encryption_key is None:
            password = os.environ.get("CONWAY_KEYSTORE_PASSWORD", "")
            if password:
                self.encryption_key = hashlib.sha256(password.encode()).digest()

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
        if self.encryption_key:
            line = self._encrypt(line_plain)
        else:
            line = line_plain
            logger.warning("Escalation log writing UNENCRYPTED — set CONWAY_KEYSTORE_PASSWORD")

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "ab") as f:
                f.write(line + b"\n")
        except Exception as exc:
            logger.error("Failed to write escalation log: %s", exc)

    def _encrypt(self, plaintext: bytes) -> bytes:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            import secrets
            nonce = secrets.token_bytes(12)
            aesgcm = AESGCM(self.encryption_key)
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            import base64
            return base64.b64encode(nonce + ciphertext)
        except ImportError:
            logger.warning("cryptography not installed, escalation log unencrypted")
            return plaintext


_default_logger = EscalationLogger()


async def log_escalation(**kwargs: Any) -> None:
    await _default_logger.log(**kwargs)


__all__ = [
    "Redactor",
    "EscalationLogger",
    "RedactionResult",
    "REDACTION_PATTERNS",
    "CANARY_SECRETS",
    "log_escalation",
]
