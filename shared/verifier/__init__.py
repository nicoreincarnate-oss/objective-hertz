"""4-layer verifier — Phase 42.5 quality safety net.

Every local LLM call passes through 4 layers BEFORE the daemon trusts the
output:

  Layer 1 — Grammar constraint (always-on for structured calls)
            outlines GBNF compiled from tool JSON schemas at startup
            → catches malformed JSON / wrong-shape tool calls

  Layer 2 — Conditional self-consistency (ONLY when low-confidence)
            n=3 sample at temp 0.3, vote on canonical hash
            → catches hallucination on hard prompts

  Layer 3 — External check (per-task validators, daemon-side via A2A)
            pytest in sandbox (Ruflo), citation round-trip (Deerflow),
            PII linter (Titan), schema validation (Conway)
            → catches semantic correctness failures

  Layer 4 — Per-daemon chain depth guard
            Ruflo=3, Titan=4, Openjarvis=4, Clawdbot=5, others=8
            → forces escalation when chain compounding gets dangerous

Failures escalate to cloud with previous_attempt_failed context.
"""

from shared.verifier.grammar_compiler import (
    GrammarCompiler,
    compile_grammar_from_schema,
)
from shared.verifier.consistency import (
    SelfConsistencyChecker,
    check_self_consistency,
)
from shared.verifier.depth_guard import (
    DepthGuard,
    PER_DAEMON_DEPTH_CAPS,
    check_depth,
)
from shared.verifier.a2a_callback import (
    DaemonSideVerifier,
    register_verifier,
    request_verification,
)

__all__ = [
    "GrammarCompiler",
    "compile_grammar_from_schema",
    "SelfConsistencyChecker",
    "check_self_consistency",
    "DepthGuard",
    "PER_DAEMON_DEPTH_CAPS",
    "check_depth",
    "DaemonSideVerifier",
    "register_verifier",
    "request_verification",
]
