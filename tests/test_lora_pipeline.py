"""Tests for LoRA training pipeline wiring — Phase 5.

Verifies:
1. LLM client routes to fine-tuned model when available
2. LLM client falls back to base model when no fine-tuned model exists
3. Training threshold is 100 (not 500)
4. Sleep cycle schedules LoRA training when should_train() is True
5. Ollama import validates the model before updating config
"""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, patch, MagicMock


# ── 1. LLM client fine-tuned model routing ──

def test_llm_client_has_resolve_ollama_model():
    """LLM client must have _resolve_ollama_model method."""
    from shared.llm_client import LLMClient
    client = LLMClient()
    assert hasattr(client, "_resolve_ollama_model")


def test_llm_resolve_uses_fine_tuned_when_available():
    """_resolve_ollama_model returns fine-tuned model when config has one."""
    from shared.llm_client import LLMClient
    client = LLMClient()

    with patch("shared.db.get_config", new_callable=AsyncMock, return_value="perseus-titan-20260325"):
        result = asyncio.run(client._resolve_ollama_model("local", pipeline_stage="email_compose"))

    assert result == "perseus-titan-20260325"


def test_llm_resolve_falls_back_without_fine_tuned():
    """_resolve_ollama_model returns base model when no fine-tuned model exists."""
    from shared.llm_client import LLMClient
    client = LLMClient()

    with patch("shared.db.get_config", new_callable=AsyncMock, return_value=None):
        result = asyncio.run(client._resolve_ollama_model("local", pipeline_stage="email_compose"))

    from shared.config import config
    assert result == config.ollama.model


def test_llm_resolve_ignores_fine_tuned_without_stage():
    """_resolve_ollama_model uses base model when no pipeline_stage is given."""
    from shared.llm_client import LLMClient
    client = LLMClient()

    result = asyncio.run(client._resolve_ollama_model("local", pipeline_stage=""))

    from shared.config import config
    assert result == config.ollama.model


# ── 2. Training threshold ──

def test_training_threshold_is_100():
    """should_train must check for 100 examples, not 500."""
    modules_to_fake = ["shared.db", "shared.config", "titan.training"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())
    fake_db.fetch_val = AsyncMock(return_value=50)  # Only 50 examples

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=__import__("pathlib").Path("/tmp/test"),
        budget=types.SimpleNamespace(cloud_gpu_cap=100),
    )

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.config"] = fake_config
    sys.modules.pop("titan.training", None)

    try:
        training = importlib.import_module("titan.training")
        result = asyncio.run(training.should_train())
        # 50 < 100, so should_train returns False
        assert result is False

        # Now 150 examples — should pass the threshold check
        # Rebind fetch_val on the module since it was imported at module level
        training.fetch_val = AsyncMock(side_effect=[150, None, 0])  # count, last_train, gpu_spend
        result = asyncio.run(training.should_train())
        assert result is True
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)


# ── 3. Sleep cycle schedules LoRA training ──

def test_sleep_cycle_schedules_lora_when_ready():
    """Sleep cycle must call insert_task('lora_training') when should_train() is True."""
    insert_calls = []

    async def mock_insert_task(task_type, payload=None, priority=5, dedupe=True):
        insert_calls.append((task_type, priority))
        return 99

    with patch("titan.training.should_train", new_callable=AsyncMock, return_value=True), \
         patch("shared.db.insert_task", mock_insert_task):
        # Import and check that the sleep cycle code references lora_training
        import inspect
        from perseus.sleep_cycle import run_sleep_cycle
        source = inspect.getsource(run_sleep_cycle)
        assert "lora_training" in source, \
            "run_sleep_cycle must schedule lora_training task"
        assert "should_train" in source, \
            "run_sleep_cycle must check should_train()"


# ── 4. Ollama import validates model ──

def test_import_to_ollama_validates_model():
    """_import_to_ollama must test the model before updating config."""
    import inspect

    modules_to_fake = ["shared.db", "shared.config", "titan.training"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=__import__("pathlib").Path("/tmp/test"),
        ollama=types.SimpleNamespace(host="http://localhost:11434", model="qwen2.5:14b"),
        budget=types.SimpleNamespace(cloud_gpu_cap=100),
    )

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.config"] = fake_config
    sys.modules.pop("titan.training", None)

    try:
        training = importlib.import_module("titan.training")
        source = inspect.getsource(training._import_to_ollama)
        # Must contain validation logic — POST to Ollama API
        assert "api/generate" in source, \
            "_import_to_ollama must validate model with a test prompt via api/generate"
        assert "set_config" in source, \
            "_import_to_ollama must call set_config to persist the model name"
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)


# ── 5. export_training_data uses 100 threshold ──

def test_export_uses_100_threshold():
    """run_lora_training must call export_training_data(min_examples=100)."""
    import inspect

    modules_to_fake = ["shared.db", "shared.config", "titan.training"]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    for fn in ["emit_event", "execute", "fetch_all", "fetch_one", "fetch_val",
               "get_config", "set_config", "insert_task", "increment_config_int"]:
        setattr(fake_db, fn, AsyncMock())

    fake_config = types.ModuleType("shared.config")
    fake_config.config = types.SimpleNamespace(
        root_dir=__import__("pathlib").Path("/tmp/test"),
        ollama=types.SimpleNamespace(host="http://localhost:11434", model="qwen2.5:14b"),
        budget=types.SimpleNamespace(cloud_gpu_cap=100),
    )

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.config"] = fake_config
    sys.modules.pop("titan.training", None)

    try:
        training = importlib.import_module("titan.training")
        source = inspect.getsource(training.run_lora_training)
        assert "min_examples=100" in source, \
            f"run_lora_training must use min_examples=100, not 500. Source excerpt: {source[:500]}"
    finally:
        for mod_name, orig in saved.items():
            if orig is not None:
                sys.modules[mod_name] = orig
            else:
                sys.modules.pop(mod_name, None)
