"""
Multi-Round Negotiation — Constraint-aware deal negotiation.

Papers: AgenticPay (Paper 71, 92% agreement rate, 15% increase in total welfare),
Strategic Tradeoffs (Paper 72, LLMs overpay without hard constraints).

Instead of one-shot proposal → close, supports multi-round negotiation:
1. Classify reply intent (price_objection, feature_request, timing_concern, ready_to_buy)
2. Generate counter-offer respecting hard price floors + max discount rules
3. Track rounds with NegotiationState
4. Final offer after max_rounds exceeded

Gated behind NEGOTIATION_ENABLED=1 (default 1).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("perseus.titan.negotiation")

NEGOTIATION_ENABLED = os.environ.get("NEGOTIATION_ENABLED", "1") == "1"

# Hard constraints (Strategic Tradeoffs paper: LLMs overpay without these)
PRICE_FLOOR = 149       # absolute minimum — never go below
WARNING_THRESHOLD = 249  # below this → flag for human review
MAX_DISCOUNT_PCT = 25   # max discount from original price
MAX_ROUNDS = 5          # max negotiation rounds before final offer


@dataclass
class NegotiationRound:
    """A single round of negotiation."""
    round_number: int
    our_offer: dict  # {"price": float, "services": list, "terms": str}
    their_response: str = ""
    intent: str = ""  # price_objection, feature_request, timing_concern, ready_to_buy, walkaway
    created_at: str = ""


@dataclass
class NegotiationState:
    """Full negotiation state for a client."""
    client_id: int
    original_price: float
    current_price: float
    rounds: list[NegotiationRound] = field(default_factory=list)
    status: str = "active"  # active, closed, lost, final_offer
    constraints: dict = field(default_factory=lambda: {
        "price_floor": PRICE_FLOOR,
        "max_discount_pct": MAX_DISCOUNT_PCT,
        "max_rounds": MAX_ROUNDS,
    })

    @property
    def round_number(self) -> int:
        return len(self.rounds)

    @property
    def discount_pct(self) -> float:
        if self.original_price <= 0:
            return 0.0
        return (1 - self.current_price / self.original_price) * 100

    @property
    def at_floor(self) -> bool:
        return self.current_price <= PRICE_FLOOR

    @property
    def max_rounds_reached(self) -> bool:
        return self.round_number >= MAX_ROUNDS


async def classify_reply_intent(reply_text: str) -> str:
    """Classify prospect's negotiation reply intent.

    Returns: price_objection | feature_request | timing_concern | ready_to_buy | walkaway
    """
    if not reply_text:
        return "walkaway"

    try:
        from shared.llm_client import llm
        result = await llm.generate(
            f"Classify this sales reply intent. Return ONLY one of: "
            f"price_objection, feature_request, timing_concern, ready_to_buy, walkaway\n\n"
            f"Reply: {reply_text[:500]}",
            model="fast",
            temperature=0.0,
            operation="titan.classify_reply_intent",
            daemon_name="titan",
        )
        intent = result.strip().lower().replace(" ", "_")
        valid = {"price_objection", "feature_request", "timing_concern", "ready_to_buy", "walkaway"}
        return intent if intent in valid else "price_objection"
    except Exception:
        return "price_objection"


async def generate_counter_offer(
    state: NegotiationState,
    reply_text: str,
    intent: str,
) -> dict:
    """Generate counter-offer respecting hard constraints.

    Strategic Tradeoffs paper: hard floors prevent LLM from over-conceding.
    Returns: {"price": float, "response_text": str, "is_final": bool}
    """
    # Calculate limits
    min_price = max(PRICE_FLOOR, state.original_price * (1 - MAX_DISCOUNT_PCT / 100))
    can_discount = state.current_price > min_price

    if intent == "ready_to_buy":
        return {
            "price": state.current_price,
            "response_text": "Great! Let's finalize at the current terms.",
            "is_final": True,
        }

    if intent == "walkaway":
        return {
            "price": state.current_price,
            "response_text": "",
            "is_final": True,
            "lost": True,
        }

    if state.max_rounds_reached:
        return {
            "price": state.current_price,
            "response_text": f"This is our best offer at ${state.current_price:.0f}. We'd love to work with you.",
            "is_final": True,
        }

    # Generate counter based on intent
    try:
        from shared.llm_client import llm

        if intent == "price_objection" and can_discount:
            # Small concession — 5-10% of remaining gap to floor
            gap = state.current_price - min_price
            concession = min(gap * 0.15, 30)  # max $30 per round
            new_price = max(min_price, state.current_price - concession)
        elif intent == "feature_request":
            new_price = state.current_price  # don't change price for features
        elif intent == "timing_concern":
            new_price = state.current_price  # address timing, not price
        else:
            new_price = state.current_price

        # Flag if below warning threshold
        if new_price < WARNING_THRESHOLD:
            logger.warning(f"Negotiation: price ${new_price:.0f} below warning threshold ${WARNING_THRESHOLD}")

        # Generate response text
        context = (
            f"You are negotiating a website deal. Current price: ${state.current_price:.0f}. "
            f"New price: ${new_price:.0f}. Round {state.round_number + 1}/{MAX_ROUNDS}. "
            f"Their concern: {intent}. Their message: {reply_text[:300]}. "
            f"{'This is approaching our final offer.' if state.round_number >= MAX_ROUNDS - 2 else ''}"
        )
        response_text = await llm.generate(
            f"{context}\nWrite a professional, warm counter-offer response (3-4 sentences).",
            model="smart",
            temperature=0.4,
            operation="titan.generate_counter_offer",
            daemon_name="titan",
        )

        return {
            "price": round(new_price, 0),
            "response_text": response_text,
            "is_final": state.round_number >= MAX_ROUNDS - 1,
        }

    except Exception as e:
        logger.debug(f"Counter-offer generation failed: {e}")
        return {
            "price": state.current_price,
            "response_text": "Let me look into this and get back to you.",
            "is_final": False,
        }


async def handle_negotiation_reply(client_id: int, reply_text: str) -> dict:
    """Main entry point: process a prospect's negotiation reply.

    1. Load/create negotiation state
    2. Classify reply intent
    3. Generate counter-offer with constraints
    4. Record round
    5. If closed → transition lead

    Returns: {"status": str, "counter_offer": dict, "state": NegotiationState}
    """
    if not NEGOTIATION_ENABLED:
        return {"status": "disabled"}

    # Load existing state or create new
    state = await _load_state(client_id)
    if not state:
        return {"status": "no_negotiation_active"}

    if state.status != "active":
        return {"status": state.status, "message": "Negotiation already concluded"}

    # Classify intent
    intent = await classify_reply_intent(reply_text)

    # Generate counter-offer
    counter = await generate_counter_offer(state, reply_text, intent)

    # Record round
    rnd = NegotiationRound(
        round_number=state.round_number + 1,
        our_offer={"price": counter["price"]},
        their_response=reply_text[:500],
        intent=intent,
    )
    state.rounds.append(rnd)
    state.current_price = counter["price"]

    # Handle outcomes
    if counter.get("lost"):
        state.status = "lost"
    elif counter.get("is_final") and intent == "ready_to_buy":
        state.status = "closed"
    elif counter.get("is_final"):
        state.status = "final_offer"

    # Persist
    await _save_round(client_id, rnd)

    return {
        "status": state.status,
        "counter_offer": counter,
        "round": state.round_number,
        "discount_pct": round(state.discount_pct, 1),
    }


async def start_negotiation(client_id: int, original_price: float) -> NegotiationState:
    """Start a new negotiation for a client."""
    state = NegotiationState(
        client_id=client_id,
        original_price=original_price,
        current_price=original_price,
    )
    # Could persist to DB here
    return state


async def _load_state(client_id: int) -> NegotiationState | None:
    """Load negotiation state from DB."""
    try:
        from shared.db import fetch_all
        rounds = await fetch_all(
            "SELECT * FROM negotiation_rounds WHERE client_id = %s ORDER BY round_number ASC",
            (client_id,),
        )
        if not rounds:
            return None

        state = NegotiationState(
            client_id=client_id,
            original_price=rounds[0].get("our_offer", {}).get("price", 299),
            current_price=rounds[-1].get("our_offer", {}).get("price", 299),
        )
        for r in rounds:
            state.rounds.append(NegotiationRound(
                round_number=r.get("round_number", 0),
                our_offer=r.get("our_offer", {}),
                their_response=r.get("their_response", ""),
                intent=r.get("intent", ""),
            ))
        return state
    except Exception:
        return None


async def _save_round(client_id: int, rnd: NegotiationRound) -> None:
    """Save a negotiation round to DB."""
    try:
        from shared.db import execute
        await execute(
            """INSERT INTO negotiation_rounds (client_id, round_number, our_offer, their_response, intent)
               VALUES (%s, %s, %s, %s, %s)""",
            (client_id, rnd.round_number, json.dumps(rnd.our_offer), rnd.their_response, rnd.intent),
        )
    except Exception as e:
        logger.debug(f"Save negotiation round failed: {e}")
