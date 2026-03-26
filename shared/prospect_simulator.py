"""
Prospect Simulator — Third persona in Alpha/Beta debate.

Papers: Self-EvolveRec (Paper 73, qualitative simulation layer),
DebateQD (Paper 66, diverse persuasion strategy evaluation).

Instead of just Alpha (propose) + Beta (challenge), add a Prospect
persona that role-plays how a typical lead would react to proposed
changes. This catches changes that look good on paper but would
actually alienate prospects.

Gated behind PROSPECT_SIM_ENABLED=1 (default 1).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("perseus.prospect_simulator")

PROSPECT_SIM_ENABLED = os.environ.get("PROSPECT_SIM_ENABLED", "1") == "1"

# Prospect personas (different segments react differently)
PERSONAS = [
    {
        "name": "Small Business Owner (Budget-Conscious)",
        "traits": "Time-poor, skeptical of cold outreach, compares prices to DIY options like Wix/Squarespace. "
                  "Values directness and specific ROI numbers. Annoyed by generic sales language.",
    },
    {
        "name": "Growing Business (Quality-Focused)",
        "traits": "Has budget but wants proof of quality. Looks for portfolios, testimonials, and guarantees. "
                  "Responds to professionalism and specific industry knowledge.",
    },
    {
        "name": "Skeptical Prospect",
        "traits": "Has been burned by agencies before. Defensive about pricing. "
                  "Needs strong trust signals. Will ghost if anything feels pushy or automated.",
    },
]


async def simulate_prospect_reactions(
    proposals: list[dict],
    snapshot: dict,
) -> list[dict]:
    """Run prospect simulation on surviving Alpha/Beta proposals.

    For each proposal, a prospect persona evaluates:
    - Would this change make me MORE or LESS likely to reply?
    - Would this change make me trust this company MORE or LESS?
    - Would I unsubscribe if I received this kind of email?

    Returns: list of {"proposal_index": int, "reaction": str, "reasoning": str, "persona": str}
    Reactions: "would_reply" | "would_ignore" | "would_unsubscribe"
    """
    if not PROSPECT_SIM_ENABLED or not proposals:
        return []

    results = []
    # Pick one persona per simulation (rotate based on proposal index)
    for i, proposal in enumerate(proposals):
        persona = PERSONAS[i % len(PERSONAS)]

        try:
            from shared.llm_client import llm
            result = await llm.generate(
                f"You are a prospect persona: {persona['name']}\n"
                f"Traits: {persona['traits']}\n\n"
                f"The AI sales system is considering this change:\n"
                f"WHAT: {proposal.get('what', '')}\n"
                f"WHERE: {proposal.get('where', '')}\n"
                f"WHY: {proposal.get('why', '')}\n\n"
                f"Current email style: {snapshot.get('soul_copy_preview', 'Professional, warm, data-driven')[:200]}\n"
                f"Current pricing: {snapshot.get('pricing', '$299 for 5-page website')}\n\n"
                f"As this prospect, how would you react to emails/proposals that incorporate this change?\n"
                f"Return JSON: {{\n"
                f'  "reaction": "would_reply" or "would_ignore" or "would_unsubscribe",\n'
                f'  "reasoning": "2-3 sentences explaining why from the prospect perspective"\n'
                f"}}",
                model="smart",
                temperature=0.5,
            )

            start = result.find("{")
            end = result.rfind("}") + 1
            parsed = json.loads(result[start:end])

            results.append({
                "proposal_index": i,
                "reaction": parsed.get("reaction", "would_ignore"),
                "reasoning": parsed.get("reasoning", ""),
                "persona": persona["name"],
            })

        except Exception as e:
            logger.debug(f"Prospect simulation failed for proposal {i}: {e}")
            results.append({
                "proposal_index": i,
                "reaction": "would_ignore",  # default: neutral
                "reasoning": f"simulation failed: {e}",
                "persona": persona["name"],
            })

    # Log results
    for r in results:
        if r["reaction"] == "would_unsubscribe":
            logger.warning(f"Prospect sim VETO: proposal {r['proposal_index']} — {r['persona']}: {r['reasoning'][:100]}")

    return results


def filter_by_prospect_reactions(
    proposals: list[dict],
    reactions: list[dict],
) -> list[dict]:
    """Filter proposals based on prospect reactions.

    Removes proposals where ANY persona would unsubscribe.
    Keeps proposals where at least one persona would reply.
    """
    if not reactions:
        return proposals

    vetoed_indices = set()
    for r in reactions:
        if r.get("reaction") == "would_unsubscribe":
            vetoed_indices.add(r["proposal_index"])

    filtered = [p for i, p in enumerate(proposals) if i not in vetoed_indices]

    if len(filtered) < len(proposals):
        logger.info(f"Prospect simulator: vetoed {len(proposals) - len(filtered)} proposals")

    return filtered
