"""Tests for Week 10: MetaClaw, dynamic routing, adaptive dashboard, scientific loop, ALMA."""

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock

os.environ["METACLAW_ENABLED"] = "1"
os.environ["DYNAMIC_DAEMON_ENABLED"] = "1"
os.environ["ADAPTIVE_DASHBOARD_ENABLED"] = "1"
os.environ["SCIENTIFIC_LOOP_ENABLED"] = "1"
os.environ["ALMA_META_MEMORY_ENABLED"] = "1"

_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_val = AsyncMock(return_value=None)

_fake_config = types.ModuleType("shared.config")
from pathlib import Path

_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="", qdrant_host="", qdrant_collection="", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
)
_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="ok"))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from shared.adaptive_dashboard import compute_dashboard_layout, detect_bottleneck
from tests.helpers.dynamic_routing import (
    classify_query_difficulty,
    get_model_for_difficulty,
    get_pipeline_config,
)
from shared.magma import meta_search_params
from shared.metaclaw import fast_adapt, get_meta_stats, slow_consolidate
from tests.helpers.scientific_loop import create_experiment, evaluate_experiments

# ═══════════════════════════════════════════════════════════════
# MetaClaw
# ═══════════════════════════════════════════════════════════════

def test_fast_adapt_returns_strategy():
    result = asyncio.run(fast_adapt({"industry": "dental", "region": "TX", "lead_score": 80}))
    assert "template" in result
    assert "pricing_tier" in result
    assert result["pricing_tier"] == "premium"  # score 80 > 75

def test_fast_adapt_low_score():
    result = asyncio.run(fast_adapt({"industry": "dental", "lead_score": 30}))
    assert result["pricing_tier"] == "budget"

def test_fast_adapt_standard():
    result = asyncio.run(fast_adapt({"industry": "dental", "lead_score": 55}))
    assert result["pricing_tier"] == "standard"

def test_slow_consolidate_empty():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(slow_consolidate())
    assert result["updated"] == 0

def test_meta_stats():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    result = asyncio.run(get_meta_stats())
    assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════
# Dynamic Routing
# ═══════════════════════════════════════════════════════════════

def test_classify_premium():
    lead = {"lead_score": 85, "email": "a@b.com", "research_facts": {"x": 1}, "deal_amount": 600}
    assert classify_query_difficulty(lead) == "premium"

def test_classify_lightweight():
    lead = {"lead_score": 20}
    assert classify_query_difficulty(lead) == "lightweight"

def test_classify_standard():
    lead = {"lead_score": 55, "email": "a@b.com"}
    assert classify_query_difficulty(lead) == "standard"

def test_model_for_lightweight():
    assert get_model_for_difficulty("lightweight", "compose") == "fast"

def test_model_for_premium():
    assert get_model_for_difficulty("premium", "research") == "genius"

def test_pipeline_config_lightweight_skips_demo():
    cfg = get_pipeline_config("lightweight")
    assert cfg["skip_demo_build"] is True
    assert cfg["use_template_only"] is True

def test_pipeline_config_premium():
    cfg = get_pipeline_config("premium")
    assert cfg["skip_demo_build"] is False
    assert cfg.get("opus_red_team") is True


# ═══════════════════════════════════════════════════════════════
# Adaptive Dashboard
# ═══════════════════════════════════════════════════════════════

def test_dashboard_default_revenue():
    result = compute_dashboard_layout({})
    assert result["layout"] == "revenue"

def test_dashboard_high_error_rate():
    # Reset cooldown
    import shared.adaptive_dashboard as ad
    ad._last_layout_change = 0
    result = compute_dashboard_layout({"error_rate": 0.10})
    assert result["layout"] == "health"
    assert "health_panel" in result["promoted_panels"]

def test_dashboard_bottleneck():
    import shared.adaptive_dashboard as ad
    ad._last_layout_change = 0
    result = compute_dashboard_layout({"bottleneck_stage": "email_send"})
    assert result["layout"] == "bottleneck"

def test_dashboard_budget_pressure():
    import shared.adaptive_dashboard as ad
    ad._last_layout_change = 0
    result = compute_dashboard_layout({"budget_remaining_pct": 0.1})
    assert result["layout"] == "budget"

def test_detect_bottleneck_none():
    assert detect_bottleneck({}) == ""

def test_detect_bottleneck_found():
    counts = {"discovered": 2, "researched": 3, "email_sent": 50, "interested": 5}
    assert detect_bottleneck(counts) == "email_sent"

def test_detect_bottleneck_balanced():
    counts = {"discovered": 10, "researched": 10, "email_sent": 10}
    assert detect_bottleneck(counts) == ""


# ═══════════════════════════════════════════════════════════════
# Scientific Loop
# ═══════════════════════════════════════════════════════════════

def test_create_experiment():
    from unittest.mock import patch
    with patch("shared.db.execute", new=AsyncMock()), \
         patch("shared.db.fetch_val", new=AsyncMock(return_value=1)):
        result = asyncio.run(create_experiment(
            "Changing send time to 9am will increase reply rate by 5%",
            "Updated send schedule in email_send.py",
            "reply_rate", 0.02, 0.001, cycle_id=1,
        ))
        assert result == 1

def test_evaluate_experiments_empty():
    _fake_db.fetch_all = AsyncMock(return_value=[])
    results = asyncio.run(evaluate_experiments())
    assert results == []


# ═══════════════════════════════════════════════════════════════
# ALMA Meta-Memory
# ═══════════════════════════════════════════════════════════════

def test_meta_params_default():
    _fake_db.get_config = AsyncMock(return_value=None)
    params = asyncio.run(meta_search_params("unknown_type"))
    assert "beam_width" in params
    assert "lambda_structural" in params

def test_meta_params_email_compose():
    _fake_db.get_config = AsyncMock(return_value=None)
    params = asyncio.run(meta_search_params("email_compose"))
    assert params["lambda_semantic"] == 0.7  # email needs more semantic

def test_meta_params_sleep_cycle():
    _fake_db.get_config = AsyncMock(return_value=None)
    params = asyncio.run(meta_search_params("sleep_cycle_analysis"))
    assert params["beam_width"] == 80  # sleep cycle needs broader search
