"""
Dynamic Daemon Allocation — Route by lead difficulty.

Paper: MaAS (Paper 79, ICML 2025 Oral, 6-45% of inference cost).

Instead of all leads getting the same 4-daemon treatment, classify
query difficulty and route accordingly:
- Lightweight: template email, Haiku-only, skip ClawdBot demo
- Standard: normal pipeline, Sonnet for compose
- Premium: full 4-daemon, Opus for red-team, extra research depth

Gated behind DYNAMIC_DAEMON_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.dynamic_routing")

DYNAMIC_DAEMON_ENABLED = os.environ.get("DYNAMIC_DAEMON_ENABLED", "1") == "1"


def classify_query_difficulty(lead: dict) -> str:
    """Classify a lead into difficulty tier based on signals.

    Returns: "lightweight" | "standard" | "premium"

    MaAS paper: dynamic allocation saves 6-45% inference cost.
    """
    if not DYNAMIC_DAEMON_ENABLED:
        return "standard"

    score = lead.get("lead_score", 50)
    has_email = bool(lead.get("email"))
    has_research = bool(lead.get("research_facts"))
    industry = lead.get("industry", "")
    deal_amount = lead.get("deal_amount", 0)

    # Premium signals
    premium_signals = 0
    if score > 75:
        premium_signals += 1
    if deal_amount > 500:
        premium_signals += 1
    if has_research and has_email:
        premium_signals += 1

    # Lightweight signals
    lightweight_signals = 0
    if score < 40:
        lightweight_signals += 1
    if not has_email:
        lightweight_signals += 1
    if not industry:
        lightweight_signals += 1

    if premium_signals >= 2:
        return "premium"
    elif lightweight_signals >= 2:
        return "lightweight"
    return "standard"


def get_model_for_difficulty(difficulty: str, task: str = "compose") -> str:
    """Select LLM model based on difficulty tier.

    Returns model name for llm_client routing.
    """
    model_map = {
        "lightweight": {"compose": "fast", "research": "fast", "close": "fast", "default": "fast"},
        "standard": {"compose": "smart", "research": "smart", "close": "smart", "default": "smart"},
        "premium": {"compose": "smart", "research": "genius", "close": "genius", "default": "smart"},
    }
    tier = model_map.get(difficulty, model_map["standard"])
    return tier.get(task, tier["default"])


def get_pipeline_config(difficulty: str) -> dict:
    """Get pipeline configuration for a difficulty tier.

    Controls which stages run and with what parameters.
    """
    configs = {
        "lightweight": {
            "skip_demo_build": True,
            "skip_red_team": True,
            "max_research_depth": 1,
            "email_variants": 1,
            "use_template_only": True,
        },
        "standard": {
            "skip_demo_build": False,
            "skip_red_team": False,
            "max_research_depth": 3,
            "email_variants": 2,
            "use_template_only": False,
        },
        "premium": {
            "skip_demo_build": False,
            "skip_red_team": False,
            "max_research_depth": 5,
            "email_variants": 3,
            "use_template_only": False,
            "extra_enrichment": True,
            "opus_red_team": True,
        },
    }
    return configs.get(difficulty, configs["standard"])
