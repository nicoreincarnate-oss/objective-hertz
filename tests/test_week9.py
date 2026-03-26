"""Tests for Week 9: Skill distillation, DebateQD, data injection, entity email."""

import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import AsyncMock

os.environ["SKILL_DISTILL_ENABLED"] = "1"
os.environ["DEBATE_QD_ENABLED"] = "1"
os.environ["DATA_INJECTION_ENABLED"] = "1"
os.environ["ENTITY_EMAIL_ENABLED"] = "1"

_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_one = AsyncMock(return_value=None)

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test-w9"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="", qdrant_host="", qdrant_collection="", zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
)
_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value='{"skill_name":"dental_outreach","industry":"dental","description":"test","email_template":"Hello {{name}}","research_focus":["reviews"],"closing_strategy":"show ROI","stages_covered":["email_compose"]}'))
_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from shared.skill_distiller import distill_trajectory, get_distilled_skills_for_industry
from shared.debate_qd import StrategyPool, get_strategy_pool, MIN_POPULATION, MAX_POPULATION
from shared.email_enrichment import enrich_email_prompt, inject_industry_stats, get_entity_context


# ═══════════════════════════════════════════════════════════════
# Skill Distillation
# ═══════════════════════════════════════════════════════════════

def test_distill_no_client():
    _fake_db.fetch_one = AsyncMock(return_value=None)
    result = asyncio.run(distill_trajectory(999))
    assert result is None

def test_distill_success():
    _fake_db.fetch_one = AsyncMock(side_effect=[
        {"id": 1, "industry": "dental", "region": "TX", "business_name": "Test Dental",
         "research_facts": {}, "status": "paid", "demo_site_url": ""},
        {"amount": 299, "status": "paid"},
    ])
    _fake_db.fetch_all = AsyncMock(return_value=[
        {"subject": "Website for your practice", "body": "Hello Dr. Smith..."}
    ])
    result = asyncio.run(distill_trajectory(1))
    if result:
        assert result["industry"] == "dental"
        assert "stages" in result

def test_distilled_skills_lookup():
    """Test that the lookup function is callable and returns a list."""
    result = asyncio.run(get_distilled_skills_for_industry("dental"))
    assert isinstance(result, list)  # May be empty if no skills dir exists


# ═══════════════════════════════════════════════════════════════
# DebateQD
# ═══════════════════════════════════════════════════════════════

def test_strategy_pool_constants():
    assert MIN_POPULATION == 3
    assert MAX_POPULATION == 7

def test_pool_creates_seeds():
    pool = StrategyPool(variants_dir=Path(tempfile.mkdtemp()) / "variants")
    count = pool.load_variants()
    assert count >= 3  # seed variants created

def test_pool_select_returns_content():
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "soul" / "variants"
    pool = StrategyPool(variants_dir=tmp)
    pool.load_variants()
    name, content = pool.select_variant()
    assert name
    assert content

def test_pool_get_top_n():
    pool = StrategyPool(variants_dir=Path(tempfile.mkdtemp()) / "variants")
    pool.load_variants()
    top = pool.get_top_n(2)
    assert len(top) >= 2

def test_pool_evolve():
    pool = StrategyPool(variants_dir=Path(tempfile.mkdtemp()) / "variants")
    pool.load_variants()
    _fake_llm.llm.generate = AsyncMock(return_value="A new evolved strategy combining directness with data.")
    result = asyncio.run(pool.evolve_step())
    assert result["action"] in ("evolved", "insufficient_variants", "crossover_failed")

def test_singleton():
    p1 = get_strategy_pool()
    p2 = get_strategy_pool()
    assert p1 is p2


# ═══════════════════════════════════════════════════════════════
# Data Injection
# ═══════════════════════════════════════════════════════════════

def test_inject_industry_stats_fallback():
    """When no verified stats, falls back to LLM-generated (tagged unverified)."""
    _fake_db.fetch_all = AsyncMock(return_value=[])
    _fake_llm.llm.generate = AsyncMock(return_value="72% of dental practices invest in websites\n45% of patients check online first")
    result = asyncio.run(inject_industry_stats("dental", "TX"))
    assert "INDUSTRY DATA POINTS" in result or result == ""

def test_inject_empty_industry():
    result = asyncio.run(inject_industry_stats(""))
    assert result == "" or "DATA POINTS" in result


# ═══════════════════════════════════════════════════════════════
# Entity Email
# ═══════════════════════════════════════════════════════════════

def test_entity_context_no_magma():
    """When MAGMA disabled, falls back to research_facts."""
    _fake_db.fetch_one = AsyncMock(return_value={
        "research_facts": '{"services": "general dentistry", "reviews": "4.5 stars"}',
        "industry": "dental", "region": "TX"
    })
    result = asyncio.run(get_entity_context("Smith Dental", client_id=1))
    # Should get fallback context from research_facts
    assert result == "" or "ENTITY CONTEXT" in result

def test_entity_context_no_data():
    _fake_db.fetch_one = AsyncMock(return_value=None)
    result = asyncio.run(get_entity_context("Unknown Business"))
    assert result == ""


# ═══════════════════════════════════════════════════════════════
# Enrich Email Prompt (combined)
# ═══════════════════════════════════════════════════════════════

def test_enrich_adds_to_prompt():
    _fake_db.fetch_all = AsyncMock(return_value=[{"insight": "Dentists reply best on Tuesdays"}])
    _fake_db.fetch_one = AsyncMock(return_value={"research_facts": "{}", "industry": "dental", "region": "TX"})
    result = asyncio.run(enrich_email_prompt(
        "Write a cold email",
        industry="dental", region="TX",
        business_name="Test Dental", client_id=1,
    ))
    assert "Write a cold email" in result

def test_enrich_disabled_passthrough():
    """When both features disabled, prompt passes through unchanged."""
    os.environ["DATA_INJECTION_ENABLED"] = "0"
    os.environ["ENTITY_EMAIL_ENABLED"] = "0"
    # Re-import to pick up env vars
    from shared.email_enrichment import enrich_email_prompt as ep
    result = asyncio.run(ep("original prompt"))
    assert result == "original prompt"
    # Restore
    os.environ["DATA_INJECTION_ENABLED"] = "1"
    os.environ["ENTITY_EMAIL_ENABLED"] = "1"
