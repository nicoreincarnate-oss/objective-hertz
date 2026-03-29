"""Tests for shared/agent_dna.py — DNA loader and circuit breaker."""

import os

from shared.agent_dna import (
    AgentDNA,
    DNACircuitBreaker,
    get_circuit_breaker,
    get_dna,
    is_dna_enabled,
)
from shared.contracts import DNAProvider

# ── DNAProvider Protocol ──────────────────────────────────────────────


class TestDNAProviderProtocol:
    def test_agent_dna_implements_dna_provider(self):
        dna = AgentDNA("titan")
        assert isinstance(dna, DNAProvider)

    def test_all_daemons_implement_protocol(self):
        for name in ("titan", "perseus", "hermes", "clawdbot", "conway"):
            dna = AgentDNA(name)
            assert isinstance(dna, DNAProvider), f"{name} failed isinstance check"


# ── DNA Loading ───────────────────────────────────────────────────────


class TestDNALoading:
    def test_load_titan(self):
        dna = AgentDNA("titan")
        dna.load()
        text = dna.get_dna("titan")
        assert len(text) > 0
        assert "Security" in text or "security" in text.lower()

    def test_load_all_daemons(self):
        for name in ("titan", "perseus", "hermes", "clawdbot", "conway"):
            dna = AgentDNA(name)
            dna.load()
            text = dna.get_dna(name)
            assert len(text) > 0, f"{name} DNA is empty"

    def test_token_budget(self):
        dna = AgentDNA("titan")
        assert dna.get_token_budget() == 500

    def test_token_cap_enforced(self):
        dna = AgentDNA("titan")
        dna.load()
        text = dna.get_dna("titan")
        # Approximate: 4 chars per token
        approx_tokens = len(text) // 4
        assert approx_tokens <= 500, f"DNA exceeds 500 token cap: ~{approx_tokens}"

    def test_boundaries_not_empty(self):
        dna = AgentDNA("titan")
        dna.load()
        boundaries = dna.get_boundaries()
        assert len(boundaries) > 0

    def test_check_permitted_tool(self):
        dna = AgentDNA("titan")
        dna.load()
        assert dna.check_action("scrape", "firecrawl_scrape") is True

    def test_check_forbidden_tool(self):
        dna = AgentDNA("titan")
        dna.load()
        assert dna.check_action("hack", "shell_exec") is False

    def test_unknown_daemon_loads_gracefully(self):
        dna = AgentDNA("nonexistent")
        dna.load()
        # Should still have universal DNA
        text = dna.get_dna("nonexistent")
        # Universal DNA may still load even without daemon profile
        assert isinstance(text, str)

    def test_boundaries_for_all_daemons(self):
        expected = {
            "titan": "Never modify wallet balances directly",
            "perseus": "Never execute pipeline stages directly",
            "hermes": "Never make business decisions",
            "clawdbot": "Never send emails to leads",
            "conway": "Never transfer funds without budget guard approval",
        }
        for name, expected_boundary in expected.items():
            dna = AgentDNA(name)
            dna.load()
            boundaries = dna.get_boundaries()
            assert any(expected_boundary in b for b in boundaries), (
                f"{name} missing expected boundary: {expected_boundary}"
            )


# ── Circuit Breaker ───────────────────────────────────────────────────


class TestDNACircuitBreaker:
    def test_starts_closed(self):
        cb = DNACircuitBreaker()
        assert cb.is_open() is False

    def test_stays_closed_under_threshold(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # 90% success rate = 10% error rate, below 15% threshold
        for _ in range(90):
            cb.record(True)
        for _ in range(10):
            cb.record(False)
        assert cb.is_open() is False

    def test_trips_above_threshold(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # 80% success rate = 20% error rate, above 15% threshold
        for _ in range(80):
            cb.record(True)
        for _ in range(20):
            cb.record(False)
        assert cb.is_open() is True

    def test_auto_reset_after_50_successes(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # Trip it
        for _ in range(80):
            cb.record(True)
        for _ in range(20):
            cb.record(False)
        assert cb.is_open() is True
        # 50 consecutive successes should reset
        for _ in range(50):
            cb.record(True)
        assert cb.is_open() is False

    def test_no_reset_before_50_successes(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # Trip it
        for _ in range(80):
            cb.record(True)
        for _ in range(20):
            cb.record(False)
        assert cb.is_open() is True
        # Only 49 successes — still tripped
        for _ in range(49):
            cb.record(True)
        assert cb.is_open() is True

    def test_manual_reset(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # Trip it
        for _ in range(70):
            cb.record(True)
        for _ in range(30):
            cb.record(False)
        assert cb.is_open() is True
        cb.reset()
        assert cb.is_open() is False
        assert cb.window_size == 0

    def test_error_rate_property(self):
        cb = DNACircuitBreaker()
        assert cb.error_rate == 0.0
        cb.record(True)
        assert cb.error_rate == 0.0
        cb.record(False)
        assert cb.error_rate == 0.5

    def test_needs_minimum_sample(self):
        cb = DNACircuitBreaker(baseline_error_rate=0.05)
        # Even all failures, < 10 samples shouldn't trip
        for _ in range(9):
            cb.record(False)
        assert cb.is_open() is False


# ── Feature Flag ──────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_by_default(self):
        os.environ.pop("ENABLE_DNA_PROFILES", None)
        assert is_dna_enabled() is False

    def test_enabled_when_set(self):
        os.environ["ENABLE_DNA_PROFILES"] = "true"
        try:
            assert is_dna_enabled() is True
        finally:
            os.environ.pop("ENABLE_DNA_PROFILES", None)

    def test_get_dna_returns_empty_when_disabled(self):
        os.environ.pop("ENABLE_DNA_PROFILES", None)
        result = get_dna("titan")
        assert result == ""

    def test_get_dna_returns_content_when_enabled(self):
        os.environ["ENABLE_DNA_PROFILES"] = "true"
        try:
            result = get_dna("titan")
            assert len(result) > 0
        finally:
            os.environ.pop("ENABLE_DNA_PROFILES", None)


# ── Module Singleton ──────────────────────────────────────────────────


class TestModuleSingleton:
    def test_get_circuit_breaker_returns_instance(self):
        cb = get_circuit_breaker()
        assert isinstance(cb, DNACircuitBreaker)
