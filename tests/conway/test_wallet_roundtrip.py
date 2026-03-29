"""
Regression tests for FIX-01: Conway wallet INSERT column order.

Proves that the INSERT INTO conway_wallets statement uses the correct
column order (agent_name, chain, public_address, keystore_ref) matching
the DB schema from 007-conway-tables.sql.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_account():
    """Mock eth_account.Account with deterministic wallet data."""
    fake_acct = MagicMock()
    fake_acct.address = "0x" + "a" * 40
    fake_acct.key.hex.return_value = "0x" + "b" * 64

    with patch.dict("sys.modules", {"eth_account": MagicMock()}):
        import eth_account

        eth_account.Account.create.return_value = fake_acct
        eth_account.Account.encrypt.return_value = {"version": 3}
        yield eth_account.Account


@pytest.fixture
def mock_db():
    """Mock shared.db.execute and shared.db.fetch_one."""
    with (
        patch("shared.db.execute", new_callable=AsyncMock) as mock_exec,
        patch("shared.db.fetch_one", new_callable=AsyncMock) as mock_fetch,
    ):
        # Default: no existing wallet in DB
        mock_fetch.return_value = None
        yield mock_exec, mock_fetch


@pytest.mark.asyncio
async def test_insert_column_order(tmp_path, mock_account, mock_db):
    """INSERT INTO conway_wallets uses (agent_name, chain, public_address, keystore_ref) order."""
    mock_exec, _mock_fetch = mock_db

    with patch("conway.wallet._get_keystore_password", return_value="test-secret"):
        from conway.wallet import WalletManager

        mgr = WalletManager(keystore_dir=str(tmp_path))
        await mgr.get_or_create_wallet("titan")

    # Verify the INSERT was called
    assert mock_exec.call_count == 1
    call_args = mock_exec.call_args

    sql = call_args[0][0]
    params = call_args[0][1]

    # Column order must match schema: agent_name, chain, public_address, keystore_ref
    assert "INSERT INTO conway_wallets (agent_name, chain, public_address, keystore_ref)" in sql

    # Params must be in same order
    assert params[0] == "titan"  # agent_name
    assert params[1] == "base"  # chain
    assert params[2] == "0x" + "a" * 40  # public_address
    assert params[3] == "titan.json"  # keystore_ref


@pytest.mark.asyncio
async def test_get_or_create_idempotent(tmp_path, mock_account, mock_db):
    """Second call to get_or_create_wallet returns cached wallet without DB write."""
    mock_exec, _mock_fetch = mock_db

    with patch("conway.wallet._get_keystore_password", return_value="test-secret"):
        from conway.wallet import WalletManager

        mgr = WalletManager(keystore_dir=str(tmp_path))
        wallet1 = await mgr.get_or_create_wallet("titan")
        wallet2 = await mgr.get_or_create_wallet("titan")

    # Same wallet object returned
    assert wallet1 is wallet2
    # execute called only once (the INSERT)
    assert mock_exec.call_count == 1


@pytest.mark.asyncio
async def test_wallet_address_format(tmp_path, mock_account, mock_db):
    """Created wallet has a valid Ethereum address format."""
    _mock_exec, _mock_fetch = mock_db

    with patch("conway.wallet._get_keystore_password", return_value="test-secret"):
        from conway.wallet import WalletManager

        mgr = WalletManager(keystore_dir=str(tmp_path))
        wallet = await mgr.get_or_create_wallet("titan")

    assert wallet.address.startswith("0x")
    assert len(wallet.address) == 42
