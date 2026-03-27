"""Tests for Week 3: Test-time learning, SCoRe self-correction, MemRL, convergence, domain segregation."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock

# ── Fake modules ──
_fake_db = types.ModuleType("shared.db")
for attr in ("emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
             "get_config", "set_config", "init_pool", "close_pool", "insert_task", "transaction"):
    setattr(_fake_db, attr, AsyncMock(return_value=None))
_fake_db.fetch_all = AsyncMock(return_value=[])

_fake_config = types.ModuleType("shared.config")
from pathlib import Path

_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password="",
                                 mem0_host="http://localhost:8888", qdrant_host="", qdrant_collection="",
                                 zep_url="", zep_enabled=False),
    ollama=types.SimpleNamespace(host="http://localhost:11434", embed_model="nomic-embed-text"),
)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="test output"))

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.llm_client"] = _fake_llm
sys.modules["shared.comms"] = _fake_comms

from shared.execution_loop import Step
from shared.magma import _resolve_domain
from shared.test_time_learning import (
    _ttrl_buffer,
    convergence_check,
    score_self_correct,
)

# ═══════════════════════════════════════════════════════════════
# SCoRe Self-Correction
# ═══════════════════════════════════════════════════════════════

def test_score_self_correct_passes_first_time():
    """If first attempt passes, return immediately."""
    async def checker(text):
        return {"passed": True}
    gen = AsyncMock(return_value="corrected")
    result = asyncio.run(score_self_correct("prompt", "good output", checker, gen))
    assert result["attempts"] == 1
    assert result["improved"] is False
    assert result["result"] == "good output"

def test_score_self_correct_improves():
    """If first attempt fails and correction passes, return corrected."""
    call_count = [0]
    async def checker(text):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"passed": False, "error": "too generic"}
        return {"passed": True}
    gen = AsyncMock(return_value="much better corrected output")
    result = asyncio.run(score_self_correct("prompt", "bad output", checker, gen, max_retries=1))
    assert result["improved"] is True
    assert result["result"] == "much better corrected output"

def test_score_self_correct_gives_up():
    """If all attempts fail check, none pass — but best is returned."""
    async def checker(text):
        return {"passed": False, "error": "still bad"}
    gen = AsyncMock(return_value="still not great")
    result = asyncio.run(score_self_correct("prompt", "bad", checker, gen, max_retries=2))
    assert result["attempts"] >= 2
    # Even though nothing "passed", the result is the best attempt
    assert result["result"] is not None


# ═══════════════════════════════════════════════════════════════
# Convergence Detection
# ═══════════════════════════════════════════════════════════════

def test_convergence_too_few_scores():
    assert convergence_check([0.5]) is False

def test_convergence_plateau():
    assert convergence_check([0.5, 0.5]) is True

def test_convergence_improving():
    assert convergence_check([0.3, 0.5, 0.7]) is False

def test_convergence_oscillating():
    """Oscillating scores → converged (no direction)."""
    assert convergence_check([0.3, 0.7, 0.3, 0.7]) is True

def test_convergence_small_delta():
    assert convergence_check([0.50, 0.51]) is True  # delta 0.01 < 0.02 threshold

def test_convergence_large_delta():
    assert convergence_check([0.3, 0.6]) is False  # delta 0.3 > 0.02


# ═══════════════════════════════════════════════════════════════
# TTRL Buffer
# ═══════════════════════════════════════════════════════════════

def test_ttrl_buffer_starts_empty():
    _ttrl_buffer.clear()
    assert len(_ttrl_buffer) == 0

def test_ttrl_disabled_by_default():
    """When TTRL_GRADIENT_ENABLED=0, buffer operations return False."""
    from shared.test_time_learning import ttrl_gradient_update
    result = asyncio.run(ttrl_gradient_update("model", "prompt", "output", 1.0))
    assert result is False


# ═══════════════════════════════════════════════════════════════
# Domain Segregation
# ═══════════════════════════════════════════════════════════════

def test_resolve_domain_client():
    assert _resolve_domain({"client_id": 42}) == "client:42"

def test_resolve_domain_industry():
    assert _resolve_domain({"industry": "Dental"}) == "industry:dental"

def test_resolve_domain_default():
    assert _resolve_domain({}) == "default"

def test_resolve_domain_client_takes_precedence():
    """Client ID should take precedence over industry."""
    result = _resolve_domain({"client_id": 5, "industry": "dental"})
    assert result == "client:5"


# ═══════════════════════════════════════════════════════════════
# Step Dataclass Upgrades
# ═══════════════════════════════════════════════════════════════

def test_step_has_self_correction_field():
    step = Step(name="test", prompt="do something")
    assert hasattr(step, "use_self_correction")
    assert step.use_self_correction is False

def test_step_has_memory_injection_field():
    step = Step(name="test", prompt="do something")
    assert hasattr(step, "use_memory_injection")
    assert step.use_memory_injection is False

def test_step_self_correction_enabled():
    step = Step(name="test", prompt="do something", use_self_correction=True)
    assert step.use_self_correction is True
