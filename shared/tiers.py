"""Centralized LLM tier definitions for Perseus Phase 41.

Tier-based routing replaces hardcoded model names. Every LLM call specifies a tier
(genius/smart/codex/agentic/longctx/chat/fast/cheap/local/local-heavy/vision) and
the LiteLLM gateway picks the best available provider for that tier with automatic
fallback.

Architectural decision (locked 2026-04-07):
- Default coding: Claude Sonnet 4.6 (79.6% SWE-Verified, best agentic, 1M ctx)
- Architecture: Claude Opus 4.6 (80.9% SWE-Verified)
- Heavy local: Llama 3.3 70B / Qwen 2.5 72B via AirLLM (disk-streamed, free)
- Vision: Kimi K2.5 (92.3% OCRBench, 6x cheaper than Sonnet)
- Cheap bulk: DeepSeek V4 (1T params on Huawei Ascend, no NVIDIA lock-in)
- Local fast: Qwen3-30B-A3B MLX-4bit (100-130 tok/s on M4 Max, native tool calls)
- License: NEVER a factor in selection. Operator decision 2026-04-07.

Compound system math: ~70% local + ~25% verifier-caught local with cloud retry +
~5% always-cloud heavy thinking ≈ 85-90% effective Claude Opus 4.6 quality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("perseus.tiers")


class TierName(str, Enum):
    """Logical tier names. These are stable identifiers used in daemon code.

    The actual model behind each tier is configured in config/litellm_config.yaml
    and resolved at request time by the LiteLLM proxy. A daemon never knows or
    cares whether 'smart' is currently Sonnet 4.6 or Kimi K2.5 — only the tier
    name matters.
    """

    GENIUS       = "genius"        # Claude Opus 4.6 — architecture, mega-plans (80.9% SWE)
    SMART        = "smart"         # Claude Sonnet 4.6 — DEFAULT agentic coding (79.6% SWE)
    CODEX        = "codex"         # GPT-5.3-Codex — one-shot code generation
    AGENTIC      = "agentic"       # MiMo-V2-Pro 1T — long-horizon, production-validated
    LONGCTX      = "longctx"       # Gemini 3.1 Pro — 1M+ context research
    CHAT         = "chat"          # Qwen 3.6 Plus — operator chat, briefings
    FAST         = "fast"          # Claude Haiku 4.5 — fanout, tool calls
    CHEAP        = "cheap"         # DeepSeek V4 — bulk classify, scheduled jobs
    LOCAL        = "local"         # Qwen3-30B-A3B MLX — local fast (Mac Studio)
    LOCAL_SMALL  = "local-small"   # Ollama secondary (llama3.2:3b) — classification only
    LOCAL_HEAVY  = "local-heavy"   # Llama 3.3 70B / Qwen 72B via AirLLM (disk-streamed)
    VISION       = "vision"        # Kimi K2.5 — best OCR + vision-language
    EMBED        = "embed"         # nomic-embed-text — embedding model (not generative)

    @classmethod
    def from_string(cls, value: str) -> TierName:
        """Parse a tier name with legacy aliases."""
        value = value.lower().strip()
        aliases = {
            "auto": "smart",
            "primary": "smart",
            "default": "smart",
            "claude": "smart",
            "claude-sonnet": "smart",
            "sonnet": "smart",
            "opus": "genius",
            "claude-opus": "genius",
            "haiku": "fast",
            "ollama": "local",
            "airllm": "local-heavy",
            "huge-context-local": "local-heavy",
            "research-local": "local-heavy",
        }
        resolved = aliases.get(value, value)
        try:
            return cls(resolved)
        except ValueError:
            logger.warning("Unknown tier %r, defaulting to SMART", value)
            return cls.SMART


@dataclass(frozen=True)
class TierConfig:
    """Static metadata for a tier. Cost numbers are advisory; actual billing comes
    from LiteLLM proxy callbacks based on real provider invoices.
    """

    name: TierName
    description: str
    primary_model: str            # Provider-prefixed model ID (anthropic/claude-sonnet-4-6, openrouter/...)
    cost_per_m_input: float       # USD per million input tokens
    cost_per_m_output: float      # USD per million output tokens
    context_window: int           # Max input tokens
    max_output_tokens: int        # Max output tokens per call
    swe_bench_verified: float     # Percentage (0-100), 0 if N/A
    fallback_chain: list[TierName]
    typical_latency_s: float
    use_when: list[str]
    avoid_when: list[str]
    is_local: bool = False        # True if runs on the Studio (no cloud cost)
    supports_vision: bool = False
    supports_tool_calls: bool = True


# ============================================================================
# Tier registry — single source of truth for all daemon LLM routing decisions
# ============================================================================

TIERS: dict[TierName, TierConfig] = {
    TierName.GENIUS: TierConfig(
        name=TierName.GENIUS,
        description="Architecture, mega-plans, novel problem solving",
        primary_model="anthropic/claude-opus-4-6",
        cost_per_m_input=5.00,
        cost_per_m_output=25.00,
        context_window=1_000_000,
        max_output_tokens=128_000,
        swe_bench_verified=80.9,
        fallback_chain=[TierName.AGENTIC, TierName.SMART, TierName.LOCAL_HEAVY, TierName.LOCAL],
        typical_latency_s=30.0,
        use_when=[
            "mega-plan synthesis",
            "alpha/beta debate arbitration",
            "architecture decisions",
            "system-wide refactors",
            "novel algorithm design",
            "Openjarvis workflow synthesis (>4 steps)",
        ],
        avoid_when=[
            "simple edits (use fast)",
            "tool calls (use fast)",
            "lookups (use cheap or local)",
            "batched bulk work (use cheap)",
        ],
    ),

    TierName.SMART: TierConfig(
        name=TierName.SMART,
        description="DEFAULT workhorse — daemon coding, section generation, planning",
        primary_model="anthropic/claude-sonnet-4-6",
        cost_per_m_input=3.00,
        cost_per_m_output=15.00,
        context_window=1_000_000,
        max_output_tokens=128_000,
        swe_bench_verified=79.6,
        fallback_chain=[TierName.AGENTIC, TierName.CODEX, TierName.LOCAL_HEAVY, TierName.LOCAL],
        typical_latency_s=15.0,
        use_when=[
            "Titan stages 7-10 (final outbound, pricing, negotiation)",
            "Clawdbot final HTML/CSS (customer-facing)",
            "Deerflow final operator-facing briefs",
            "Ruflo Aider architect calls",
            "default escalation from local",
        ],
        avoid_when=[
            "architecture (use genius)",
            "high-volume bulk (use cheap)",
            "simple lookups (use local or cheap)",
        ],
    ),

    TierName.CODEX: TierConfig(
        name=TierName.CODEX,
        description="One-shot code generation, SOTA on SWE-Bench Pro",
        primary_model="openai/gpt-5.3-codex",
        cost_per_m_input=1.75,
        cost_per_m_output=14.00,
        context_window=400_000,
        max_output_tokens=128_000,
        swe_bench_verified=80.0,
        fallback_chain=[TierName.SMART, TierName.LOCAL_HEAVY, TierName.LOCAL],
        typical_latency_s=12.0,
        use_when=[
            "single-file code generation",
            "one-shot function implementation",
            "script generation for spikes",
        ],
        avoid_when=[
            "multi-file refactors (use smart)",
            "long agent loops (use smart or agentic)",
        ],
    ),

    TierName.AGENTIC: TierConfig(
        name=TierName.AGENTIC,
        description="Long-horizon multi-step loops, production-validated at 4.65T tok/wk",
        primary_model="openrouter/xiaomi/mimo-v2-pro",
        cost_per_m_input=0.50,
        cost_per_m_output=2.00,
        context_window=256_000,
        max_output_tokens=64_000,
        swe_bench_verified=75.0,
        fallback_chain=[TierName.SMART, TierName.LOCAL_HEAVY, TierName.LOCAL],
        typical_latency_s=18.0,
        use_when=[
            "Clawdbot section_orchestrator parallel agents",
            "Titan pipeline multi-step sequences (stages 1-6)",
            "agent tool loops spanning >5 steps",
            "high-volume agentic workloads",
        ],
        avoid_when=[
            "single-shot generation (use codex)",
            "architecture decisions (use genius)",
        ],
    ),

    TierName.LONGCTX: TierConfig(
        name=TierName.LONGCTX,
        description="1M+ context for research synthesis and codebase analysis",
        primary_model="gemini/gemini-3.1-pro",
        cost_per_m_input=2.00,
        cost_per_m_output=8.00,
        context_window=2_000_000,
        max_output_tokens=64_000,
        swe_bench_verified=80.6,
        fallback_chain=[TierName.SMART, TierName.LOCAL_HEAVY, TierName.LOCAL],
        typical_latency_s=25.0,
        use_when=[
            "Deerflow synthesis across many sources",
            "full codebase analysis (>100k tokens)",
            "long conversation history",
        ],
        avoid_when=[
            "short prompts (wasteful)",
            "real-time latency-sensitive tasks",
        ],
    ),

    TierName.CHAT: TierConfig(
        name=TierName.CHAT,
        description="Operator chat, briefings, non-coding dialogue",
        primary_model="openrouter/qwen/qwen-3.6-plus",
        cost_per_m_input=0.40,
        cost_per_m_output=1.20,
        context_window=256_000,
        max_output_tokens=32_000,
        swe_bench_verified=70.0,
        fallback_chain=[TierName.SMART, TierName.CHEAP, TierName.LOCAL],
        typical_latency_s=6.0,
        use_when=[
            "Hermes Telegram chat",
            "Jarvis voice mode responses (intent disambiguation)",
            "Hermes briefing generation",
            "non-coding Q&A",
        ],
        avoid_when=[
            "coding tasks (use smart/codex)",
            "structured agent loops (use agentic)",
        ],
    ),

    TierName.FAST: TierConfig(
        name=TierName.FAST,
        description="Fanout, tool calls, simple edits, prompt-cache friendly",
        primary_model="anthropic/claude-haiku-4-5",
        cost_per_m_input=1.00,
        cost_per_m_output=5.00,
        context_window=200_000,
        max_output_tokens=64_000,
        swe_bench_verified=73.3,
        fallback_chain=[TierName.CHEAP, TierName.LOCAL],
        typical_latency_s=4.0,
        use_when=[
            "subagent fanout",
            "tool result summarization",
            "log analysis",
            "simple classification",
            "Aider editor calls (when local-coder is unavailable)",
        ],
        avoid_when=[
            "multi-step reasoning (use smart)",
            "architecture (use genius)",
        ],
    ),

    TierName.CHEAP: TierConfig(
        name=TierName.CHEAP,
        description="Batched bulk work, DeepSeek V4 on Huawei Ascend (no NVIDIA lock-in)",
        primary_model="openrouter/deepseek/deepseek-v4",
        cost_per_m_input=0.27,
        cost_per_m_output=1.10,
        context_window=256_000,
        max_output_tokens=32_000,
        swe_bench_verified=74.0,
        fallback_chain=[TierName.FAST, TierName.LOCAL],
        typical_latency_s=10.0,
        use_when=[
            "bulk lead enrichment (non-critical)",
            "scheduled batch jobs",
            "cost-sensitive high-volume tasks",
            "shadow-mode A/B comparisons",
        ],
        avoid_when=[
            "customer-facing output (use smart)",
            "revenue-critical paths (use smart)",
        ],
    ),

    TierName.LOCAL: TierConfig(
        name=TierName.LOCAL,
        description="Qwen3-30B-A3B MLX-4bit on Mac Studio (100-130 tok/s, native tool calls)",
        primary_model="ollama/qwen3:30b-a3b-mlx-4bit",
        cost_per_m_input=0.0,
        cost_per_m_output=0.0,
        context_window=32_000,
        max_output_tokens=16_000,
        swe_bench_verified=67.0,
        fallback_chain=[TierName.CHEAP, TierName.SMART],  # Escalate UP if local can't handle
        typical_latency_s=8.0,
        use_when=[
            "Hermes alerts, briefings, Telegram chat (high volume)",
            "Perseus log triage, scheduler narration",
            "Deerflow source scoring, brief drafts",
            "Conway ledger reconciliation",
            "Titan stages 1-6 (lead extraction, scoring, draft emails)",
            "Openjarvis DAG node labels, telemetry",
            "default tier when AUTO_TIER_ENABLED=true",
        ],
        avoid_when=[
            "customer-facing outputs (escalate to smart)",
            "code generation >50 LOC (escalate to smart or local-heavy)",
            "heavy reasoning / mega-plans (escalate to local-heavy or genius)",
        ],
        is_local=True,
        supports_tool_calls=True,
    ),

    # NOTE 2026-04-07: LOCAL_HEAVY tier is DEFERRED until Samsung T9 2TB Thunderbolt arrives.
    # On the interim 1000 MB/s USB-C drive, AirLLM streaming runs at ~1-2 tok/s on Llama 70B,
    # which is too slow to be useful (~3-4 minutes per response). Until T9 lands, all heavy
    # thinking calls escalate to cloud Opus 4.6 instead. The tier definition stays here so the
    # routing config doesn't break — calls to LOCAL_HEAVY currently fall through to GENIUS via
    # the fallback chain below.
    TierName.LOCAL_HEAVY: TierConfig(
        name=TierName.LOCAL_HEAVY,
        description="Llama 3.3 70B / Qwen 2.5 72B via AirLLM (DEFERRED until T9 NVMe; cloud Opus until then)",
        primary_model="airllm/meta-llama/Llama-3.3-70B-Instruct",
        cost_per_m_input=0.0,
        cost_per_m_output=0.0,
        context_window=128_000,
        max_output_tokens=8_000,
        swe_bench_verified=72.0,
        fallback_chain=[TierName.GENIUS, TierName.SMART],  # Until T9 arrives, route LOCAL_HEAVY to cloud Opus
        typical_latency_s=120.0,  # 3-8 tok/s on M4 Max with T9 NVMe → ~30-90s for typical responses
        use_when=[
            "Openjarvis mega-plans (rare, latency tolerant)",
            "Ruflo Aider architect for cross-file refactors (latency tolerant)",
            "Conway strategic agent economics decisions",
            "any 'heavy thinking' call where >30s latency is acceptable",
            "fully private reasoning (no cloud)",
        ],
        avoid_when=[
            "user-facing latency budget <10s (use smart instead)",
            "high-frequency calls (use local or smart)",
        ],
        is_local=True,
        supports_tool_calls=False,  # AirLLM doesn't support tool-calling format reliably
    ),

    TierName.LOCAL_SMALL: TierConfig(
        name=TierName.LOCAL_SMALL,
        description="Ollama secondary (llama3.2:3b) — classification, tag extraction, tiny lookups",
        primary_model="ollama/llama3.2:3b",
        cost_per_m_input=0.0,
        cost_per_m_output=0.0,
        context_window=8_000,
        max_output_tokens=2_000,
        swe_bench_verified=0.0,
        fallback_chain=[TierName.LOCAL, TierName.CHEAP],
        typical_latency_s=2.0,
        use_when=[
            "binary/multiclass classification",
            "tag extraction",
            "very short lookups (<500 tokens out)",
            "tone analysis",
        ],
        avoid_when=[
            "any generative coding (use local or smart)",
            "multi-step reasoning (use local or smart)",
        ],
        is_local=True,
        supports_tool_calls=False,
    ),

    TierName.EMBED: TierConfig(
        name=TierName.EMBED,
        description="nomic-embed-text — embedding model only, NOT generative",
        primary_model="ollama/nomic-embed-text",
        cost_per_m_input=0.0,
        cost_per_m_output=0.0,
        context_window=8_192,
        max_output_tokens=1,
        swe_bench_verified=0.0,
        fallback_chain=[TierName.LOCAL_SMALL],
        typical_latency_s=0.5,
        use_when=[
            "vector embeddings for memory graph",
            "semantic search indexing",
            "similarity scoring",
        ],
        avoid_when=[
            "any generative call — embed tier returns vectors, not text",
        ],
        is_local=True,
        supports_tool_calls=False,
    ),

    TierName.VISION: TierConfig(
        name=TierName.VISION,
        description="Kimi K2.5 — 92.3% OCRBench, 90.1% MathVista, BEST vision model (6x cheaper than Sonnet)",
        primary_model="openrouter/moonshotai/kimi-k2.5",
        cost_per_m_input=0.47,
        cost_per_m_output=2.00,
        context_window=256_000,
        max_output_tokens=64_000,
        swe_bench_verified=76.8,
        fallback_chain=[TierName.SMART, TierName.LOCAL],
        typical_latency_s=10.0,
        use_when=[
            "Clawdbot v2 visual QA (Phase 34)",
            "Clawdbot full-page audits",
            "Deerflow document/chart parsing",
            "Hermes Jarvis screenshot analysis",
            "Titan lead enrichment with vision",
            "OCR-heavy lead pipeline",
        ],
        avoid_when=[
            "text-only tasks (waste of vision capability)",
        ],
        supports_vision=True,
    ),
}


# ============================================================================
# Helpers
# ============================================================================

def get_tier(name: str | TierName) -> TierConfig:
    """Look up a tier config by name. Defaults to SMART on unknown input."""
    if isinstance(name, str):
        tier_enum = TierName.from_string(name)
    else:
        tier_enum = name
    return TIERS[tier_enum]


def estimate_cost(tier: TierName, input_tokens: int, output_tokens: int) -> float:
    """Calculate expected USD cost for a request. Local tiers return 0."""
    config = TIERS[tier]
    return (
        (input_tokens / 1_000_000) * config.cost_per_m_input +
        (output_tokens / 1_000_000) * config.cost_per_m_output
    )


def downgrade_tier(tier: TierName, reason: str = "") -> TierName:
    """Downgrade a tier to the next-cheaper equivalent for budget pressure.

    Order:
    - genius  → agentic (keeps reasoning power, cheaper than Opus)
    - smart   → agentic → cheap → local
    - codex   → smart → cheap → local
    - agentic → smart → cheap → local
    - longctx → chat → cheap → local
    - chat    → cheap → local
    - fast    → cheap → local
    - cheap   → local
    - local   → local-heavy (free, slow)
    - local-heavy → local (terminal local)
    - vision  → smart → local
    """
    downgrade_map = {
        TierName.GENIUS:      TierName.AGENTIC,
        TierName.SMART:       TierName.AGENTIC,
        TierName.CODEX:       TierName.SMART,
        TierName.AGENTIC:     TierName.SMART,
        TierName.LONGCTX:     TierName.CHAT,
        TierName.CHAT:        TierName.CHEAP,
        TierName.FAST:        TierName.CHEAP,
        TierName.CHEAP:       TierName.LOCAL,
        TierName.LOCAL:       TierName.LOCAL_SMALL,
        TierName.LOCAL_SMALL: TierName.LOCAL_SMALL,  # terminal (already smallest)
        TierName.LOCAL_HEAVY: TierName.LOCAL,
        TierName.VISION:      TierName.SMART,
        TierName.EMBED:       TierName.EMBED,  # embed has no downgrade
    }
    new_tier = downgrade_map.get(tier, tier)
    if new_tier != tier:
        logger.warning("Tier downgrade: %s → %s (%s)", tier.value, new_tier.value, reason)
    return new_tier


def upgrade_tier(tier: TierName, reason: str = "") -> TierName:
    """Upgrade a tier when verifier catches a local failure and we need more capability.

    Order is INVERSE of downgrade — local failures escalate UP toward cloud.
    Local → smart → agentic → genius. Local-heavy → smart → genius.
    """
    upgrade_map = {
        TierName.LOCAL:       TierName.SMART,
        TierName.LOCAL_SMALL: TierName.LOCAL,
        TierName.LOCAL_HEAVY: TierName.SMART,
        TierName.EMBED:       TierName.EMBED,
        TierName.CHEAP:       TierName.SMART,
        TierName.FAST:        TierName.SMART,
        TierName.CHAT:        TierName.SMART,
        TierName.AGENTIC:     TierName.SMART,
        TierName.SMART:       TierName.GENIUS,
        TierName.CODEX:       TierName.SMART,
        TierName.LONGCTX:     TierName.GENIUS,
        TierName.GENIUS:      TierName.GENIUS,
        TierName.VISION:      TierName.VISION,
    }
    new_tier = upgrade_map.get(tier, tier)
    if new_tier != tier:
        logger.info("Tier upgrade (verifier escalation): %s → %s (%s)", tier.value, new_tier.value, reason)
    return new_tier


def is_local(tier: TierName | str) -> bool:
    """True if a tier runs on the Mac Studio with no cloud cost."""
    config = get_tier(tier)
    return config.is_local


def supports_vision(tier: TierName | str) -> bool:
    """True if a tier can accept image inputs."""
    config = get_tier(tier)
    return config.supports_vision


def supports_tool_calls(tier: TierName | str) -> bool:
    """True if a tier reliably handles structured tool calls."""
    config = get_tier(tier)
    return config.supports_tool_calls


__all__ = [
    "TierName",
    "TierConfig",
    "TIERS",
    "get_tier",
    "estimate_cost",
    "downgrade_tier",
    "upgrade_tier",
    "is_local",
    "supports_vision",
    "supports_tool_calls",
]
