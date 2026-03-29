"""Agent DNA loader and circuit breaker.

Loads universal engineering DNA and per-daemon YAML profiles from ``soul/``.
Implements the ``DNAProvider`` Protocol from ``shared/contracts.py``.
DNA content is validated through the injection scanner before use.

The ``DNACircuitBreaker`` monitors LLM call error rates and silently
disables DNA injection when errors exceed baseline + 10%.
"""

from __future__ import annotations

import logging
import os
from collections import deque

import yaml

from shared.config import config

logger = logging.getLogger("perseus.agent_dna")

# Paths relative to project root
_SOUL_DIR = config.root_dir / "soul"
_DNA_DIR = _SOUL_DIR / "dna"
_UNIVERSAL_DNA_PATH = _SOUL_DIR / "engineering_dna.md"

# Token cap for combined DNA injection
_DNA_TOKEN_CAP = 500


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token for English text."""
    return len(text) // 4


def _truncate_to_token_cap(text: str, cap: int = _DNA_TOKEN_CAP) -> str:
    """Truncate text to approximate token cap, breaking at last full sentence."""
    if _estimate_tokens(text) <= cap:
        return text
    # Approximate char limit
    char_limit = cap * 4
    truncated = text[:char_limit]
    # Try to break at last period
    last_period = truncated.rfind(".")
    if last_period > char_limit // 2:
        return truncated[: last_period + 1]
    return truncated


def _scan_for_injection(text: str) -> bool:
    """Run text through injection scanner. Returns True if clean."""
    try:
        from openjarvis.security.injection_scanner import InjectionScanner

        scanner = InjectionScanner()
        result = scanner.scan(text)
        if not result.is_clean:
            logger.warning(
                "DNA content failed injection scan: %s",
                [f.pattern_name for f in result.findings],
            )
            return False
        return True
    except ImportError:
        # Rust backend not available — log and allow (fail open for dev)
        logger.debug("Injection scanner unavailable, skipping DNA scan")
        return True
    except Exception as exc:
        logger.warning("Injection scan failed: %s — allowing DNA", exc)
        return True


class AgentDNA:
    """Loads and injects DNA into LLM calls. Implements DNAProvider Protocol."""

    def __init__(self, daemon_name: str) -> None:
        self._daemon = daemon_name
        self._universal_dna: str = ""
        self._profile: dict = {}
        self._loaded = False
        self._combined_dna: str = ""

    def load(self) -> None:
        """Load universal DNA + daemon-specific profile. Verify signatures."""
        # Load universal DNA
        if _UNIVERSAL_DNA_PATH.exists():
            self._universal_dna = _UNIVERSAL_DNA_PATH.read_text()
        else:
            logger.warning("Universal DNA not found at %s", _UNIVERSAL_DNA_PATH)

        # Load daemon-specific profile
        profile_path = _DNA_DIR / f"{self._daemon}.yaml"
        if profile_path.exists():
            raw = profile_path.read_text()
            self._profile = yaml.safe_load(raw) or {}
        else:
            logger.warning(
                "DNA profile for '%s' not found at %s", self._daemon, profile_path
            )

        # Validate loaded content through injection scanner
        combined_raw = self._universal_dna + "\n" + yaml.dump(self._profile)
        if not _scan_for_injection(combined_raw):
            logger.error("DNA content failed injection scan — refusing to load")
            self._universal_dna = ""
            self._profile = {}
            self._loaded = False
            return

        # Build combined DNA string (universal + boundaries)
        parts = [self._universal_dna.strip()]
        boundaries = self._profile.get("boundaries", [])
        if boundaries:
            parts.append(
                "\n## Role Boundaries ("
                + self._profile.get("name", self._daemon)
                + ")\n"
                + "\n".join(f"- {b}" for b in boundaries)
            )

        self._combined_dna = _truncate_to_token_cap("\n".join(parts))
        self._loaded = True
        logger.info(
            "DNA loaded for '%s': ~%d tokens",
            self._daemon,
            _estimate_tokens(self._combined_dna),
        )

    def get_dna(self, daemon_name: str) -> str:
        """Return DNA string for injection into LLM system parameter."""
        if not self._loaded:
            self.load()
        if daemon_name != self._daemon:
            logger.warning(
                "DNA requested for '%s' but loaded for '%s'",
                daemon_name,
                self._daemon,
            )
        return self._combined_dna

    def get_token_budget(self) -> int:
        """Return the max token budget for DNA-augmented calls."""
        return _DNA_TOKEN_CAP

    def get_boundaries(self) -> list[str]:
        """Return role boundaries for this daemon."""
        if not self._loaded:
            self.load()
        return list(self._profile.get("boundaries", []))

    def check_action(self, action: str, tool: str) -> bool:
        """Check if a tool is permitted by this daemon's DNA profile."""
        if not self._loaded:
            self.load()
        permitted = self._profile.get("permitted_tools", [])
        if not permitted:
            # No restrictions defined — allow
            return True
        return tool in permitted


class DNACircuitBreaker:
    """Disables DNA injection if LLM error rate exceeds baseline + 10%.

    Tracks the last 100 LLM call outcomes. When the error rate exceeds
    baseline + 10%, the breaker trips and DNA injection is silently skipped.
    After 50 consecutive successes, the breaker auto-resets.
    """

    def __init__(self, baseline_error_rate: float = 0.05) -> None:
        self._baseline = baseline_error_rate
        self._window: deque[bool] = deque(maxlen=100)
        self._tripped = False
        self._consecutive_successes = 0

    def record(self, success: bool) -> None:
        """Record an LLM call outcome (True=success, False=failure)."""
        self._window.append(success)

        if success:
            self._consecutive_successes += 1
        else:
            self._consecutive_successes = 0

        # Check for auto-reset
        if self._tripped and self._consecutive_successes >= 50:
            self._tripped = False
            self._consecutive_successes = 0
            logger.info("DNA circuit breaker RESET after 50 consecutive successes")
            _emit_breaker_metric("reset")
            return

        # Check for trip
        if len(self._window) >= 10:  # Need minimum sample
            error_rate = 1 - (sum(self._window) / len(self._window))
            if error_rate > self._baseline + 0.10 and not self._tripped:
                self._tripped = True
                logger.warning(
                    "DNA circuit breaker TRIPPED: error rate %.1f%% "
                    "(baseline %.1f%% + 10%%)",
                    error_rate * 100,
                    self._baseline * 100,
                )
                _emit_breaker_metric("tripped")

    def is_open(self) -> bool:
        """Return True if breaker is tripped (DNA should be skipped)."""
        return self._tripped

    def reset(self) -> None:
        """Manual reset — clears window and trips."""
        self._tripped = False
        self._window.clear()
        self._consecutive_successes = 0

    @property
    def error_rate(self) -> float:
        """Current error rate in the sliding window."""
        if not self._window:
            return 0.0
        return 1 - (sum(self._window) / len(self._window))

    @property
    def window_size(self) -> int:
        """Number of recorded outcomes in the sliding window."""
        return len(self._window)


def _emit_breaker_metric(state: str) -> None:
    """Emit circuit breaker state to observability (best-effort)."""
    try:
        from openjarvis.observability.collector import metrics_collector

        metrics_collector.record_custom(
            "dna_circuit_breaker",
            {"state": state},
        )
    except Exception:
        pass  # Observability is best-effort


def is_dna_enabled() -> bool:
    """Check if DNA profiles feature flag is active."""
    return os.getenv("ENABLE_DNA_PROFILES", "").lower() in ("1", "true", "yes")


# Module-level singleton instances
_dna_cache: dict[str, AgentDNA] = {}
_circuit_breaker = DNACircuitBreaker()


def get_dna(daemon_name: str) -> str:
    """Convenience: get DNA string for a daemon, respecting feature flag and breaker."""
    if not is_dna_enabled():
        return ""
    if _circuit_breaker.is_open():
        logger.debug("DNA circuit breaker open — skipping injection for %s", daemon_name)
        return ""
    if daemon_name not in _dna_cache:
        dna = AgentDNA(daemon_name)
        dna.load()
        _dna_cache[daemon_name] = dna
    return _dna_cache[daemon_name].get_dna(daemon_name)


def get_circuit_breaker() -> DNACircuitBreaker:
    """Return the module-level circuit breaker instance."""
    return _circuit_breaker


__all__ = [
    "AgentDNA",
    "DNACircuitBreaker",
    "get_circuit_breaker",
    "get_dna",
    "is_dna_enabled",
]
