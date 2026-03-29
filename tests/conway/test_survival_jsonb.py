"""
Regression tests for FIX-03: Conway survival tier JSONB writes.

Proves that survival tier changes write JSONB-wrapped values to system_config
using psycopg's Jsonb wrapper, that tier calculation is correct for all balance
ranges, and that tier changes emit proper events and alerts.
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_wallet():
    """Create a mock wallet with configurable balance."""
    wallet = MagicMock()
    wallet.address = "0x" + "aa" * 20
    wallet.get_balance = AsyncMock(return_value=Decimal("1.0"))
    return wallet


@pytest.fixture
def mock_wallet_manager(mock_wallet):
    """Create a mock WalletManager that returns the mock wallet."""
    mgr = MagicMock()
    mgr.get_or_create_wallet = AsyncMock(return_value=mock_wallet)
    return mgr


@pytest.fixture
def mock_ledger():
    """Create a mock EconomicLedger."""
    return MagicMock()


@pytest.fixture
def mock_db_calls():
    """Patch shared.db functions as used by conway.survival (module-level imports).

    conway/survival.py imports: execute, insert_task, set_config — not fetch_one.
    """
    with (
        patch("conway.survival.execute", new_callable=AsyncMock) as mock_exec,
        patch("conway.survival.set_config", new_callable=AsyncMock) as mock_set_cfg,
        patch("conway.survival.insert_task", new_callable=AsyncMock) as mock_ins_task,
    ):
        yield {
            "execute": mock_exec,
            "set_config": mock_set_cfg,
            "insert_task": mock_ins_task,
        }


@pytest.mark.asyncio
async def test_jsonb_wrapper_used(mock_wallet, mock_wallet_manager, mock_ledger, mock_db_calls):
    """Tier change wraps value with psycopg Jsonb() when psycopg is available."""
    # Balance 1.0 maps to low_compute (>= 0.5, < 2.0 -> critical? No: >= 2.0 is low_compute)
    # Actually: normal >= 10, low_compute >= 2, critical >= 0.5, dead >= 0
    # Balance 1.0 -> critical tier
    mock_wallet.get_balance = AsyncMock(return_value=Decimal("5.0"))

    mock_jsonb_cls = MagicMock()
    mock_jsonb_instance = MagicMock()
    mock_jsonb_cls.return_value = mock_jsonb_instance

    # Patch the module-level _JsonbType directly (already imported at module load time)
    with patch("conway.survival._JsonbType", mock_jsonb_cls):
        from conway.survival import SurvivalMonitor

        monitor = SurvivalMonitor(mock_wallet_manager, mock_ledger)
        await monitor.check_and_enforce("titan")

    # Jsonb wrapper should have been called with the tier name
    mock_jsonb_cls.assert_called_once_with("low_compute")

    # The execute call for system_config should use the wrapped value
    exec_calls = mock_db_calls["execute"].call_args_list
    # First call is the INSERT INTO system_config
    config_call = exec_calls[0]
    sql = config_call[0][0]
    params = config_call[0][1]
    assert "system_config" in sql
    assert params[0] == "conway_tier_titan"
    assert params[1] is mock_jsonb_instance


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "balance,expected_tier",
    [
        (Decimal("15.0"), "normal"),
        (Decimal("5.0"), "low_compute"),
        (Decimal("1.0"), "critical"),
        (Decimal("0.5"), "critical"),
        (Decimal("0.3"), "dead"),
        (Decimal("0"), "dead"),
    ],
)
async def test_tier_value_correct_for_each_balance(
    balance, expected_tier, mock_wallet, mock_wallet_manager, mock_ledger, mock_db_calls
):
    """Tier calculation returns correct tier for each balance range."""
    mock_wallet.get_balance = AsyncMock(return_value=balance)

    # Patch psycopg Jsonb so the import inside _apply_tier_change works
    with patch("psycopg.types.json.Jsonb", MagicMock(side_effect=lambda x: x)):
        # Patch send_alert for critical tier
        with patch("shared.comms.send_alert", new_callable=AsyncMock):
            from conway.survival import SurvivalMonitor

            monitor = SurvivalMonitor(mock_wallet_manager, mock_ledger)
            result = await monitor.check_and_enforce("titan")

    assert result["tier"] == expected_tier


@pytest.mark.asyncio
async def test_tier_change_emits_event(mock_wallet, mock_wallet_manager, mock_ledger, mock_db_calls):
    """Tier change emits a survival_tier_change event."""
    mock_wallet.get_balance = AsyncMock(return_value=Decimal("5.0"))

    with patch("psycopg.types.json.Jsonb", MagicMock(side_effect=lambda x: x)):
        from conway.survival import SurvivalMonitor

        monitor = SurvivalMonitor(mock_wallet_manager, mock_ledger)
        result = await monitor.check_and_enforce("titan")

    assert result["changed"] is True

    # Second execute call should be the event insertion
    exec_calls = mock_db_calls["execute"].call_args_list
    event_call = exec_calls[1]
    sql = event_call[0][0]
    params = event_call[0][1]
    assert "survival_tier_change" in sql
    payload = json.loads(params[0])
    assert payload["agent"] == "titan"
    assert payload["new_tier"] == "low_compute"


@pytest.mark.asyncio
async def test_critical_tier_triggers_alert(mock_wallet, mock_wallet_manager, mock_ledger, mock_db_calls):
    """Critical tier triggers an alert via shared.comms.send_alert."""
    mock_wallet.get_balance = AsyncMock(return_value=Decimal("0.5"))

    with (
        patch("psycopg.types.json.Jsonb", MagicMock(side_effect=lambda x: x)),
        patch("shared.comms.send_alert", new_callable=AsyncMock) as mock_alert,
    ):
        from conway.survival import SurvivalMonitor

        monitor = SurvivalMonitor(mock_wallet_manager, mock_ledger)
        await monitor.check_and_enforce("titan")

    mock_alert.assert_called_once()
    alert_msg = mock_alert.call_args[0][0]
    assert "CRITICAL" in alert_msg
    assert "balance below" in alert_msg
