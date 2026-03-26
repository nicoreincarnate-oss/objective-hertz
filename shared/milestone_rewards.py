"""
Milestone Rewards — Per-stage reward signals for the pipeline.

Paper: MiRA (Paper 75) — Subgoal-Driven Framework for Long-Horizon Agent RL.
Gemma3-12B: 6.4%→43.0% success with milestone rewards vs GPT-4-Turbo at 17.6%.

Instead of one reward at payment (weeks after discovery), emit a reward
at EVERY stage transition. This provides dense learning signal for:
- Training data labels (collect_training_example)
- TTRL gradient buffer (test_time_learning)
- Bandit arm updates (which email variant led to this progression)

Gated behind MILESTONE_REWARDS_ENABLED=1 (default 1).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("perseus.milestone_rewards")

MILESTONE_REWARDS_ENABLED = os.environ.get("MILESTONE_REWARDS_ENABLED", "1") == "1"

# Stage transition rewards (MiRA paper: milestone-based dense reward)
# Higher rewards for harder/more valuable transitions
STAGE_REWARDS: dict[str, float] = {
    "discovered": 0.02,          # found a lead — minimal
    "researched": 0.05,          # enriched with data
    "email_drafted": 0.05,       # composed personalized email
    "email_queued": 0.02,        # queued for send
    "email_sent": 0.05,          # delivered
    "followed_up": 0.05,         # follow-up sent
    "replied": 0.15,             # prospect engaged!
    "interested": 0.30,          # expressed interest — major signal
    "demo_built": 0.20,          # invested effort in demo
    "proposal_sent": 0.15,       # proposal delivered
    "negotiating": 0.10,         # active negotiation
    "closed": 0.50,              # deal closed — high value
    "building": 0.10,            # site under construction
    "deployed": 0.15,            # site live
    "invoiced": 0.10,            # invoice sent
    "paid": 1.00,                # money received — max reward
    "lost": -0.20,               # lost lead — negative signal
    "churned": -0.30,            # customer churned — worse
}


def get_transition_reward(from_status: str, to_status: str) -> float:
    """Get reward for a specific stage transition.

    Returns reward value. Higher = more valuable transition.
    Negative for lost/churned transitions.
    """
    if not MILESTONE_REWARDS_ENABLED:
        return 0.0
    return STAGE_REWARDS.get(to_status, 0.0)


async def emit_milestone_reward(
    client_id: int,
    from_status: str,
    to_status: str,
    metadata: dict | None = None,
) -> float:
    """Emit a milestone reward and route to all learning systems.

    Called by state_machine.transition_lead() on every status change.
    Routes reward to: training data, TTRL buffer, bandit, events table.
    """
    reward = get_transition_reward(from_status, to_status)
    if reward == 0.0:
        return 0.0

    meta = metadata or {}
    logger.debug(f"Milestone reward: client={client_id} {from_status}→{to_status} reward={reward}")

    # 1. Record in events table for visibility
    try:
        from shared.db import emit_event
        await emit_event("milestone_reward", {
            "client_id": client_id,
            "from_status": from_status,
            "to_status": to_status,
            "reward": reward,
            **meta,
        })
    except Exception:
        pass

    # 2. Feed into TTRL gradient buffer (if enabled)
    try:
        from shared.test_time_learning import ttrl_gradient_update, TTRL_GRADIENT_ENABLED
        if TTRL_GRADIENT_ENABLED:
            prompt_context = meta.get("last_prompt", f"Pipeline stage: {from_status}→{to_status}")
            output_context = meta.get("last_output", f"Transition successful")
            await ttrl_gradient_update("qwen2.5:14b", prompt_context, output_context, reward)
    except Exception:
        pass

    # 3. Feed into bandit (if this transition resulted from a bandit-selected variant)
    try:
        from shared.bandit import get_bandit, BANDIT_ENABLED
        if BANDIT_ENABLED:
            experiment_id = meta.get("bandit_experiment")
            arm_name = meta.get("bandit_arm")
            if experiment_id and arm_name:
                await get_bandit().update(experiment_id, arm_name, max(0.0, min(1.0, reward)))
    except Exception:
        pass

    return reward


def compute_discounted_future_reward(current_status: str) -> float:
    """MiRA dual-critic: estimate discounted future reward from current stage.

    Based on historical conversion rates per stage.
    Used for value estimation in training.
    """
    # Approximate expected future reward based on pipeline position
    # These are rough estimates — should be calibrated from actual data
    FUTURE_REWARDS: dict[str, float] = {
        "discovered": 0.05,     # long way to go
        "researched": 0.08,
        "email_drafted": 0.10,
        "email_sent": 0.12,
        "replied": 0.25,
        "interested": 0.40,
        "demo_built": 0.50,
        "proposal_sent": 0.60,
        "negotiating": 0.70,
        "closed": 0.85,
        "deployed": 0.90,
        "invoiced": 0.95,
        "paid": 1.00,
    }
    return FUTURE_REWARDS.get(current_status, 0.0)
