"""Tests for Week 8: Multi-round negotiation, prospect simulator, milestone rewards wiring."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

os.environ["NEGOTIATION_ENABLED"] = "1"
os.environ["PROSPECT_SIM_ENABLED"] = "1"

_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task",
             "transaction", "increment_config_int"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])

_fake_config = types.ModuleType("shared.config")
from pathlib import Path
_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="", qdrant_host="", qdrant_collection="", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
)
_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="price_objection"))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from titan.negotiation import (
    MAX_ROUNDS,
    PRICE_FLOOR,
    NegotiationRound,
    NegotiationState,
    classify_reply_intent,
    generate_counter_offer,
    start_negotiation,
)
from shared.prospect_simulator import (
    PERSONAS,
    filter_by_prospect_reactions,
    simulate_prospect_reactions,
)


# ═══════════════════════════════════════════════════════════════
# NegotiationState
# ═══════════════════════════════════════════════════════════════

def test_state_initial():
    state = NegotiationState(client_id=1, original_price=299, current_price=299)
    assert state.round_number == 0
    assert state.discount_pct == 0.0
    assert not state.at_floor
    assert not state.max_rounds_reached

def test_state_discount_calculation():
    state = NegotiationState(client_id=1, original_price=299, current_price=249)
    assert 15 < state.discount_pct < 20  # ~16.7%

def test_state_at_floor():
    state = NegotiationState(client_id=1, original_price=299, current_price=149)
    assert state.at_floor

def test_state_max_rounds():
    state = NegotiationState(client_id=1, original_price=299, current_price=299)
    state.rounds = [NegotiationRound(round_number=i, our_offer={"price": 299}) for i in range(MAX_ROUNDS)]
    assert state.max_rounds_reached


# ═══════════════════════════════════════════════════════════════
# Intent Classification
# ═══════════════════════════════════════════════════════════════

def test_classify_empty_is_walkaway():
    result = asyncio.run(classify_reply_intent(""))
    assert result == "walkaway"

def test_classify_returns_valid_intent():
    _fake_llm.llm.generate = AsyncMock(return_value="price_objection")
    result = asyncio.run(classify_reply_intent("That's too expensive for us"))
    assert result in {"price_objection", "feature_request", "timing_concern", "ready_to_buy", "walkaway"}


# ═══════════════════════════════════════════════════════════════
# Counter-Offer Generation
# ═══════════════════════════════════════════════════════════════

def test_counter_ready_to_buy():
    state = NegotiationState(client_id=1, original_price=299, current_price=279)
    result = asyncio.run(generate_counter_offer(state, "Let's do it!", "ready_to_buy"))
    assert result["is_final"] is True
    assert result["price"] == 279  # no change on ready_to_buy

def test_counter_walkaway():
    state = NegotiationState(client_id=1, original_price=299, current_price=279)
    result = asyncio.run(generate_counter_offer(state, "Not interested", "walkaway"))
    assert result.get("lost") is True

def test_counter_max_rounds():
    state = NegotiationState(client_id=1, original_price=299, current_price=259)
    state.rounds = [NegotiationRound(round_number=i, our_offer={"price": 299}) for i in range(MAX_ROUNDS)]
    result = asyncio.run(generate_counter_offer(state, "Still too much", "price_objection"))
    assert result["is_final"] is True

def test_counter_respects_floor():
    """Counter-offer should never go below PRICE_FLOOR."""
    state = NegotiationState(client_id=1, original_price=299, current_price=160)
    _fake_llm.llm.generate = AsyncMock(return_value="We can adjust slightly.")
    result = asyncio.run(generate_counter_offer(state, "Way too expensive", "price_objection"))
    assert result["price"] >= PRICE_FLOOR

def test_counter_price_objection_discounts():
    """Price objection should result in small discount."""
    state = NegotiationState(client_id=1, original_price=299, current_price=299)
    _fake_llm.llm.generate = AsyncMock(return_value="Let me see what I can do.")
    result = asyncio.run(generate_counter_offer(state, "Too expensive", "price_objection"))
    assert result["price"] <= 299  # discounted or same


# ═══════════════════════════════════════════════════════════════
# Start Negotiation
# ═══════════════════════════════════════════════════════════════

def test_start_negotiation():
    state = asyncio.run(start_negotiation(42, 299.0))
    assert state.client_id == 42
    assert state.original_price == 299.0
    assert state.current_price == 299.0
    assert state.status == "active"


# ═══════════════════════════════════════════════════════════════
# Constraints
# ═══════════════════════════════════════════════════════════════

def test_price_floor_constant():
    assert PRICE_FLOOR == 149

def test_max_rounds_constant():
    assert MAX_ROUNDS == 5


# ═══════════════════════════════════════════════════════════════
# Prospect Simulator
# ═══════════════════════════════════════════════════════════════

def test_personas_exist():
    assert len(PERSONAS) >= 3
    assert all("name" in p and "traits" in p for p in PERSONAS)

def test_simulate_returns_reactions():
    _fake_llm.llm.generate = AsyncMock(return_value='{"reaction": "would_reply", "reasoning": "Looks good"}')
    proposals = [{"what": "adjust send time", "where": "config", "why": "better open rates"}]
    results = asyncio.run(simulate_prospect_reactions(proposals, {}))
    assert len(results) == 1
    assert results[0]["reaction"] in ("would_reply", "would_ignore", "would_unsubscribe")

def test_simulate_empty_proposals():
    results = asyncio.run(simulate_prospect_reactions([], {}))
    assert results == []

def test_filter_removes_unsubscribe():
    proposals = [
        {"what": "good change"},
        {"what": "bad change"},
        {"what": "ok change"},
    ]
    reactions = [
        {"proposal_index": 0, "reaction": "would_reply"},
        {"proposal_index": 1, "reaction": "would_unsubscribe"},
        {"proposal_index": 2, "reaction": "would_ignore"},
    ]
    filtered = filter_by_prospect_reactions(proposals, reactions)
    assert len(filtered) == 2
    assert filtered[0]["what"] == "good change"
    assert filtered[1]["what"] == "ok change"

def test_filter_no_reactions():
    proposals = [{"what": "anything"}]
    assert filter_by_prospect_reactions(proposals, []) == proposals

def test_filter_all_safe():
    proposals = [{"what": "a"}, {"what": "b"}]
    reactions = [
        {"proposal_index": 0, "reaction": "would_reply"},
        {"proposal_index": 1, "reaction": "would_ignore"},
    ]
    assert len(filter_by_prospect_reactions(proposals, reactions)) == 2
