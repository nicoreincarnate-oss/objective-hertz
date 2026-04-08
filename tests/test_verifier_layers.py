"""Verifier layer tests — Layer 1 (grammar), 2 (consistency), 4 (depth).

Layer 3 (daemon-side callback) requires daemon-side fixtures and is tested in
each daemon's own integration test directory.
"""

from __future__ import annotations

import pytest


class TestGrammarCompiler:
    def test_compile_simple_object(self):
        from shared.verifier.grammar_compiler import GrammarCompiler
        schema = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        compiler = GrammarCompiler(output_dir=None)
        grammar = compiler.compile(schema, daemon="test", tool_name="simple")
        assert "root" in grammar.gbnf_text
        assert "string" in grammar.gbnf_text
        assert "number" in grammar.gbnf_text

    def test_compile_array(self):
        from shared.verifier.grammar_compiler import GrammarCompiler
        schema = {
            "type": "object",
            "required": ["tags"],
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        }
        compiler = GrammarCompiler(output_dir=None)
        grammar = compiler.compile(schema, daemon="test", tool_name="arr")
        assert grammar.gbnf_text  # No crash

    def test_compile_enum(self):
        from shared.verifier.grammar_compiler import GrammarCompiler
        schema = {
            "type": "object",
            "required": ["category"],
            "properties": {
                "category": {"type": "string", "enum": ["adopt", "test", "watch", "ignore"]}
            },
        }
        compiler = GrammarCompiler(output_dir=None)
        grammar = compiler.compile(schema, daemon="test", tool_name="enum")
        assert "adopt" in grammar.gbnf_text
        assert "ignore" in grammar.gbnf_text

    def test_caches_compiled_grammars(self):
        from shared.verifier.grammar_compiler import GrammarCompiler
        compiler = GrammarCompiler(output_dir=None)
        schema = {"type": "object", "properties": {}}
        compiler.compile(schema, daemon="d1", tool_name="t1")
        cached = compiler.get("d1", "t1")
        assert cached is not None


class TestSelfConsistency:
    def test_should_not_trigger_default(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        assert checker.should_trigger() is False

    def test_should_trigger_force(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        assert checker.should_trigger(force=True) is True

    def test_should_trigger_low_logprob(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker(logprob_threshold=0.4)
        assert checker.should_trigger(logprob_margin=0.2) is True
        assert checker.should_trigger(logprob_margin=0.5) is False

    def test_should_trigger_ambiguous_schema(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        assert checker.should_trigger(ambiguous_schema=True) is True

    def test_canonical_hash_invariant_to_whitespace(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        h1 = checker._canonicalize("Hello   world")
        h2 = checker._canonicalize("hello world")
        assert h1 == h2

    def test_vote_majority(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        result = checker._vote(["yes", "yes", "no"])
        assert result.consistent is True
        assert result.chosen_response == "yes"
        assert result.vote_count == 2

    def test_vote_no_majority(self):
        from shared.verifier.consistency import SelfConsistencyChecker
        checker = SelfConsistencyChecker()
        result = checker._vote(["a", "b", "c"])
        assert result.consistent is False  # No 2/3 agreement


class TestDepthGuard:
    def test_under_cap_allowed(self):
        from shared.verifier.depth_guard import check_depth
        from shared.tiers import TierName
        result = check_depth("ruflo", chain_depth=2, current_tier=TierName.LOCAL)
        assert result.allowed is True

    def test_over_cap_blocked(self):
        from shared.verifier.depth_guard import check_depth
        from shared.tiers import TierName
        result = check_depth("ruflo", chain_depth=4, current_tier=TierName.LOCAL)
        assert result.allowed is False
        assert result.suggested_tier == TierName.SMART

    def test_per_daemon_caps(self):
        from shared.verifier.depth_guard import PER_DAEMON_DEPTH_CAPS
        assert PER_DAEMON_DEPTH_CAPS["ruflo"] == 3
        assert PER_DAEMON_DEPTH_CAPS["titan"] == 4
        assert PER_DAEMON_DEPTH_CAPS["openjarvis"] == 4
        assert PER_DAEMON_DEPTH_CAPS["clawdbot"] == 5

    def test_unknown_daemon_uses_default(self):
        from shared.verifier.depth_guard import check_depth, DEFAULT_DEPTH_CAP
        from shared.tiers import TierName
        result = check_depth("unknown", chain_depth=DEFAULT_DEPTH_CAP - 1, current_tier=TierName.LOCAL)
        assert result.allowed is True


class TestRedactor:
    def test_redacts_openai_key(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().redact("My key is sk-abc12345678901234567890ABCDEF really")
        assert "sk-abc" not in result.redacted_text
        assert "REDACTED" in result.redacted_text
        assert result.redaction_count >= 1

    def test_redacts_anthropic_key(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().redact("sk-ant-api03-" + "a" * 50)
        assert "REDACTED" in result.redacted_text

    def test_redacts_email_keeps_domain(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().redact("Contact alice@acme.com about the deal")
        assert "alice@acme.com" not in result.redacted_text
        assert "acme.com" in result.redacted_text  # Domain preserved for context

    def test_redacts_phone(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().redact("Call me at 555-123-4567 today")
        assert "555-123-4567" not in result.redacted_text

    def test_redacts_eth_address(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().redact("Wallet: 0x" + "a" * 40)
        assert "REDACTED:eth-address" in result.redacted_text

    def test_canary_test_passes(self):
        from shared.escalation_log.redactor import Redactor
        result = Redactor().canary_test()
        assert not result.canaries_missed, f"Canaries leaked: {result.canaries_missed}"
        assert len(result.canaries_caught) >= 3
