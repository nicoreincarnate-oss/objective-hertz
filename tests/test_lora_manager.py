"""Tests for shared/lora_manager.py — LoRA training lifecycle management."""

import subprocess
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.lora_manager import (
    MIN_EXAMPLES,
    TrainingStatus,
    activate_model,
    check_and_trigger_training,
    deactivate_model,
    get_ab_test_results,
    get_training_status,
    validate_fine_tuned_model,
)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    """Patch all DB functions used by lora_manager."""
    with (
        patch("shared.lora_manager.fetch_val", new_callable=AsyncMock) as fetch_val,
        patch("shared.lora_manager.fetch_all", new_callable=AsyncMock) as fetch_all,
        patch("shared.lora_manager.execute", new_callable=AsyncMock) as execute,
        patch("shared.lora_manager.get_config", new_callable=AsyncMock) as get_config,
        patch("shared.lora_manager.set_config", new_callable=AsyncMock) as set_config,
    ):
        yield {
            "fetch_val": fetch_val,
            "fetch_all": fetch_all,
            "execute": execute,
            "get_config": get_config,
            "set_config": set_config,
        }


@pytest.fixture
def mock_config():
    """Patch config.ollama for consistent test values."""
    with patch("shared.lora_manager.config") as cfg:
        cfg.ollama.model = "qwen2.5:14b-instruct-q4_K_M"
        cfg.ollama.host = "http://localhost:11434"
        yield cfg


# ── test_training_status_not_ready ───────────────────────────────


@pytest.mark.asyncio
async def test_training_status_not_ready(mock_db, mock_config):
    """With fewer than MIN_EXAMPLES, status.ready_to_train should be False."""
    mock_db["fetch_val"].side_effect = [
        30,       # example count (< 100)
        None,     # last training date
        0,        # in-progress count
    ]
    mock_db["get_config"].return_value = ""

    with patch("shared.lora_manager._check_mlx_available", return_value=True), \
         patch("shared.lora_manager._check_vastai_available", return_value=False):
        status = await get_training_status()

    assert isinstance(status, TrainingStatus)
    assert status.examples_collected == 30
    assert status.ready_to_train is False
    assert status.mlx_available is True


# ── test_training_status_ready ───────────────────────────────────


@pytest.mark.asyncio
async def test_training_status_ready(mock_db, mock_config):
    """With 100+ examples, mlx available, and no cooldown, should be ready."""
    mock_db["fetch_val"].side_effect = [
        150,      # example count
        None,     # last training date (never trained)
        0,        # in-progress count
    ]
    mock_db["get_config"].return_value = ""

    with patch("shared.lora_manager._check_mlx_available", return_value=True), \
         patch("shared.lora_manager._check_vastai_available", return_value=False):
        status = await get_training_status()

    assert status.examples_collected == 150
    assert status.ready_to_train is True
    assert status.fine_tuned_model == ""


# ── test_check_trigger_conditions ────────────────────────────────


@pytest.mark.asyncio
async def test_check_trigger_conditions():
    """When should_train returns False, training should not be triggered."""
    with patch("shared.lora_manager.should_train", new_callable=AsyncMock, return_value=False) as mock_should:
        with patch("shared.lora_manager.run_lora_training", new_callable=AsyncMock) as mock_run:
            # Patch the import path used in check_and_trigger_training
            with patch.dict("sys.modules", {}):
                # Re-patch at the function level
                with patch("titan.training.should_train", new_callable=AsyncMock, return_value=False):
                    with patch("titan.training.run_lora_training", new_callable=AsyncMock):
                        result = await check_and_trigger_training()

    assert result is False


# ── test_trigger_training_when_ready ─────────────────────────────


@pytest.mark.asyncio
async def test_trigger_training_when_ready():
    """When should_train returns True, run_lora_training should be called."""
    with patch("titan.training.should_train", new_callable=AsyncMock, return_value=True), \
         patch("titan.training.run_lora_training", new_callable=AsyncMock) as mock_run:
        result = await check_and_trigger_training()

    assert result is True
    mock_run.assert_awaited_once()


# ── test_validate_model_success ──────────────────────────────────


@pytest.mark.asyncio
async def test_validate_model_success(mock_config):
    """Validation should pass when model produces reasonable output."""
    good_response = {"response": "A" * 200}  # 200 chars, well above min_length

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = good_response

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await validate_fine_tuned_model("perseus-titan-20260404")

    assert result["valid"] is True
    assert "PASSED" in result["comparison"]
    assert len(result["scores"]) == 3


# ── test_activate_model ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_model(mock_db, mock_config):
    """Activating a model should set system_config and emit event."""
    # Mock ollama list showing the model exists
    with patch("subprocess.run") as mock_run, \
         patch("shared.db.emit_event", new_callable=AsyncMock) as mock_emit:
        mock_run.return_value = MagicMock(
            stdout="perseus-titan-20260404\nqwen2.5:14b\n",
            returncode=0,
        )

        result = await activate_model("perseus-titan-20260404")

    assert result is True
    mock_db["set_config"].assert_awaited_once_with("fine_tuned_model", "perseus-titan-20260404")


# ── test_deactivate_model ────────────────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_model(mock_db, mock_config):
    """Deactivating should clear the fine_tuned_model config."""
    mock_db["get_config"].return_value = "perseus-titan-20260404"

    with patch("shared.db.emit_event", new_callable=AsyncMock):
        result = await deactivate_model()

    assert result is True
    mock_db["set_config"].assert_awaited_once_with("fine_tuned_model", "")


# ── test_no_mlx_graceful ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_mlx_graceful(mock_db, mock_config):
    """Without mlx or Vast.ai, status should show not ready even with enough data."""
    mock_db["fetch_val"].side_effect = [
        200,      # example count (plenty)
        None,     # last training date
        0,        # in-progress count
    ]
    mock_db["get_config"].return_value = ""

    with patch("shared.lora_manager._check_mlx_available", return_value=False), \
         patch("shared.lora_manager._check_vastai_available", return_value=False):
        status = await get_training_status()

    assert status.examples_collected == 200
    assert status.ready_to_train is False
    assert status.mlx_available is False
    assert status.vastai_available is False
