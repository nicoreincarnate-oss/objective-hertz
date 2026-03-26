"""Tests for perseus/backprop.py — safe behavioral editing."""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch, mock_open

_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.execute = AsyncMock()
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_one = AsyncMock(return_value=None)
_fake_db.fetch_val = AsyncMock(return_value=None)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.init_pool = AsyncMock()
_fake_db.close_pool = AsyncMock()
_fake_db.insert_task = AsyncMock(return_value=1)
_fake_db.transaction = AsyncMock()

_fake_config = types.ModuleType("shared.config")
_fake_config.config = types.SimpleNamespace(
    root_dir=Path("/tmp/test-repo"),
    memory=types.SimpleNamespace(magma_enabled=False, neo4j_uri="", neo4j_user="", neo4j_password=""),
)

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="ok"))

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.comms"] = _fake_comms
sys.modules["shared.llm_client"] = _fake_llm

from perseus.backprop import (
    IMMUTABLE_FILES,
    PRICE_MAX,
    PRICE_MIN,
    PRICE_MAX_DELTA_PER_CYCLE,
    apply_config_change,
)


# ── Immutable protections ──

def test_immutable_files_includes_critical():
    assert any("compliance" in f for f in IMMUTABLE_FILES)
    assert any("db.py" in f for f in IMMUTABLE_FILES)

def test_immutable_files_includes_config():
    assert any("config.py" in f for f in IMMUTABLE_FILES)


# ── Pricing guardrails ──

def test_price_range():
    assert PRICE_MIN == 149
    assert PRICE_MAX == 499

def test_price_max_delta():
    assert PRICE_MAX_DELTA_PER_CYCLE == 50


# ── apply_config_change ──

def test_config_change_within_range():
    """Config change with $30 delta (within $50 max) should succeed."""
    # Patch get_config at the module level where backprop imported it
    with patch("perseus.backprop.get_config", new=AsyncMock(return_value=299)):
        with patch("perseus.backprop.set_config", new=AsyncMock()):
            with patch("perseus.backprop.record_decision", new=AsyncMock(return_value=1)):
                result = asyncio.run(apply_config_change("PRICE_WEBSITE_5PAGE", 329, "test reason", cycle_id=1))
                assert result is True

def test_config_change_exceeds_range():
    _fake_db.get_config = AsyncMock(return_value=299)
    result = asyncio.run(apply_config_change("PRICE_WEBSITE_5PAGE", 599, "test reason", cycle_id=1))
    # $599 exceeds PRICE_MAX of $499 — should be blocked
    assert result is False or result is None

def test_config_change_exceeds_delta():
    _fake_db.get_config = AsyncMock(return_value=299)
    result = asyncio.run(apply_config_change("PRICE_WEBSITE_5PAGE", 200, "test reason", cycle_id=1))
    # Delta is $99, exceeds $50 max — should be blocked
    assert result is False or result is None

def test_config_change_non_price_key():
    """Non-pricing config changes should not have pricing guardrails."""
    _fake_db.get_config = AsyncMock(return_value="old_value")
    _fake_db.set_config = AsyncMock()
    _fake_comms.record_decision = AsyncMock(return_value=1)
    result = asyncio.run(apply_config_change("SOME_OTHER_KEY", "new_value", "test reason", cycle_id=1))
    assert result is not False
