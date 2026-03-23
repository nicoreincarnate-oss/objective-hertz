from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_ensure_agent_runtime_provisions_cloud_identity_and_registry(monkeypatch, patch_db):
    from conway import runtime

    fake_wallet = SimpleNamespace(agent_name="titan", address="0xabc")

    class _WalletManager:
        async def get_or_create_wallet(self, agent_name: str):
            assert agent_name == "titan"
            return fake_wallet

    class _Registry:
        def __init__(self, contract_address: str | None = None):
            self.contract_address = contract_address

        async def is_registered(self, address: str) -> bool:
            assert address == "0xabc"
            return False

        async def register(self, wallet, agent_card):
            assert wallet is fake_wallet
            assert agent_card["name"] == "titan"
            return "0xtx123"

    class _CloudClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.available = True

    monkeypatch.setattr(
        runtime,
        "config",
        SimpleNamespace(
            conway=SimpleNamespace(
                enabled=True,
                keystore_path="tmp/keys",
                api_key="",
                api_url="https://api.conway.test",
                x402_facilitator_url="https://x402.test",
                erc8004_registry="0xregistry",
            )
        ),
    )

    result = await runtime.ensure_agent_runtime(
        "titan",
        agent_card={"name": "titan", "description": "Revenue engine"},
        wallet_manager=_WalletManager(),
        provision_api_key_fn=AsyncMock(return_value="siwe-key"),
        cloud_client_factory=_CloudClient,
        registry_factory=_Registry,
    )

    assert result["wallet_address"] == "0xabc"
    assert result["api_key_provisioned"] is True
    assert result["cloud_client_ready"] is True
    assert result["erc8004_registered"] is True
    assert result["erc8004_tx"] == "0xtx123"
    assert patch_db.tables["system_config"]["conway_api_key_titan"] == "siwe-key"
    assert patch_db.tables["system_config"]["conway_erc8004_tx_titan"] == "0xtx123"


@pytest.mark.asyncio
async def test_conway_cloud_uses_x402_fallback_on_402(monkeypatch):
    from conway.cloud import ConwayCloudClient
    import conway.cloud as cloud_mod

    class _Response:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.content = b'{"accepts": {"scheme": "exact"}}'

        def raise_for_status(self):
            if self.status_code >= 400 and self.status_code != 402:
                raise RuntimeError(f"bad status {self.status_code}")

        def json(self):
            return {"accepts": {"scheme": "exact"}}

    class _AsyncClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def request(self, method, url, headers=None, json=None):
            return _Response(402)

    monkeypatch.setattr(cloud_mod.httpx, "AsyncClient", _AsyncClient)

    wallet = SimpleNamespace(agent_name="titan", address="0xabc")
    client = ConwayCloudClient(wallet=wallet)
    client._x402 = SimpleNamespace(
        pay_and_request=AsyncMock(return_value={"status": 200, "data": {"balance": "7"}})
    )

    credits = await client.get_credits()

    assert credits == Decimal("7")
    client._x402.pay_and_request.assert_awaited_once()

