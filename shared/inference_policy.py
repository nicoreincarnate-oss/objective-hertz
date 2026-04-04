"""Central inference-lane decisions for Perseus subsystems.

This keeps model selection intentional across Kirito/Hermes, ClawdBot,
Titan, pipeline stages, and OpenJarvis background jobs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InferenceDecision:
    subsystem: str
    task: str
    primary_model: str
    rationale: str
    fallback_model: str


_DECISIONS: dict[tuple[str, str], InferenceDecision] = {
    ("kirito", "live_voice"): InferenceDecision(
        subsystem="kirito",
        task="live_voice",
        primary_model="smart",
        fallback_model="fast",
        rationale="Live operator-facing turns prioritize quality and low friction over maximum local size.",
    ),
    ("kirito", "memory_query"): InferenceDecision(
        subsystem="kirito",
        task="memory_query",
        primary_model="local-heavy",
        fallback_model="smart",
        rationale="Memory-heavy async/local reasoning benefits from AirLLM or future oLLM before cloud escalation.",
    ),
    ("clawdbot", "browser_reasoning"): InferenceDecision(
        subsystem="clawdbot",
        task="browser_reasoning",
        primary_model="smart",
        fallback_model="fast",
        rationale="Interactive browser planning needs stronger reasoning with quick recovery, not giant local context.",
    ),
    ("clawdbot", "web_research_digest"): InferenceDecision(
        subsystem="clawdbot",
        task="web_research_digest",
        primary_model="local-heavy",
        fallback_model="smart",
        rationale="Large scraped pages and multi-source synthesis are a good fit for heavy local async inference.",
    ),
    ("pipeline", "lead_discovery"): InferenceDecision(
        subsystem="pipeline",
        task="lead_discovery",
        primary_model="fast",
        fallback_model="local-small",
        rationale="Discovery ranking and query generation are latency-sensitive and repeat often.",
    ),
    ("pipeline", "lead_research"): InferenceDecision(
        subsystem="pipeline",
        task="lead_research",
        primary_model="smart",
        fallback_model="local-heavy",
        rationale="Lead research writes business-facing synthesis; quality still matters more than cheapest local inference.",
    ),
    ("pipeline", "email_compose"): InferenceDecision(
        subsystem="pipeline",
        task="email_compose",
        primary_model="smart",
        fallback_model="fast",
        rationale="Customer-facing copy needs stronger persuasion and nuance.",
    ),
    ("titan", "memory_consolidation"): InferenceDecision(
        subsystem="titan",
        task="memory_consolidation",
        primary_model="local-heavy",
        fallback_model="smart",
        rationale="Consolidation, contradiction checks, and diffing are long-context internal tasks.",
    ),
    ("titan", "negotiation"): InferenceDecision(
        subsystem="titan",
        task="negotiation",
        primary_model="smart",
        fallback_model="fast",
        rationale="Negotiation responses are external-facing and should preserve quality.",
    ),
    ("openjarvis", "background_research"): InferenceDecision(
        subsystem="openjarvis",
        task="background_research",
        primary_model="local-heavy",
        fallback_model="smart",
        rationale="Long-horizon research and synthesis should exploit heavy local models first.",
    ),
    ("openjarvis", "orchestration"): InferenceDecision(
        subsystem="openjarvis",
        task="orchestration",
        primary_model="genius",
        fallback_model="smart",
        rationale="Cross-domain orchestration remains a top-tier reasoning job.",
    ),
}


def choose_inference_lane(subsystem: str, task: str) -> InferenceDecision:
    key = (subsystem.strip().lower(), task.strip().lower())
    if key in _DECISIONS:
        return _DECISIONS[key]
    return InferenceDecision(
        subsystem=subsystem,
        task=task,
        primary_model="smart",
        fallback_model="fast",
        rationale="Default to strong general reasoning when no narrower policy exists.",
    )
