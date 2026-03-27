"""Tests for titan/training.py — LoRA training pipeline."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock

_fake_db = types.ModuleType("shared.db")
_fake_db.emit_event = AsyncMock(return_value=1)
_fake_db.execute = AsyncMock()
_fake_db.fetch_all = AsyncMock(return_value=[])
_fake_db.fetch_one = AsyncMock(return_value=None)
_fake_db.fetch_val = AsyncMock(return_value=0)
_fake_db.get_config = AsyncMock(return_value=None)
_fake_db.set_config = AsyncMock()
_fake_db.init_pool = AsyncMock()
_fake_db.close_pool = AsyncMock()
_fake_db.insert_task = AsyncMock(return_value=1)
_fake_db.transaction = AsyncMock()

_fake_config = types.ModuleType("shared.config")
from pathlib import Path as _Path

_fake_config.config = types.SimpleNamespace(
    root_dir=_Path("/tmp/test-repo"),
    budget=types.SimpleNamespace(cloud_gpu_cap=200),
    ollama=types.SimpleNamespace(host="http://localhost:11434", model="qwen2.5:14b"),
    conway=types.SimpleNamespace(enabled=False, api_url="", api_key=""),
    ruflo=types.SimpleNamespace(enabled=False),
)

_fake_comms = types.ModuleType("shared.comms")
_fake_comms.record_decision = AsyncMock(return_value=1)

_fake_llm = types.ModuleType("shared.llm_client")
_fake_llm.llm = types.SimpleNamespace(generate=AsyncMock(return_value="done"))

sys.modules["shared.db"] = _fake_db
sys.modules["shared.config"] = _fake_config
sys.modules["shared.comms"] = _fake_comms
sys.modules["shared.llm_client"] = _fake_llm

from titan.training import collect_training_example, export_training_data, should_train

# ── collect_training_example ──

def test_collect_training_example_no_crash():
    """collect_training_example should not raise even with mocked DB."""
    asyncio.run(collect_training_example(
        example_type="email_compose",
        input_text="Draft email for dentist",
        output_text="Dear Dr. Smith...",
        metadata={"client_id": 1},
    ))
    # If no exception, the function works with our mock


# ── export_training_data ──

def test_export_returns_path_or_none():
    """export_training_data returns a Path or None."""
    result = asyncio.run(export_training_data(min_examples=1))
    # With empty fetch_all, should return None (not enough data)
    assert result is None


# ── should_train ──

def test_should_train_function_exists():
    """should_train is callable and returns bool."""
    result = asyncio.run(should_train())
    assert isinstance(result, bool)
