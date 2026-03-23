"""Runtime wiring for Conway features used by live agents.

This module turns Conway from a passive library into an active runtime layer:
- wallet provisioning
- SIWE API-key provisioning
- Conway Cloud client instantiation
- optional ERC-8004 registration
"""

from __future__ import annotations

import logging
from typing import Any

from conway.cloud import ConwayCloudClient
from conway.identity import provision_api_key
from conway.registry import AgentRegistry8004, ZERO_ADDRESS
from conway.wallet import WalletManager
from shared.config import config
from shared.db import emit_event, get_config, set_config

logger = logging.getLogger("conway.runtime")


async def ensure_agent_runtime(
    agent_name: str,
    *,
    agent_card: dict[str, Any] | None = None,
    wallet_manager: WalletManager | None = None,
    provision_api_key_fn=provision_api_key,
    cloud_client_factory=ConwayCloudClient,
    registry_factory=AgentRegistry8004,
) -> dict[str, Any]:
    """Provision Conway runtime pieces for a live agent without crashing startup."""
    if not config.conway.enabled:
        return {"enabled": False, "agent": agent_name}

    result: dict[str, Any] = {
        "enabled": True,
        "agent": agent_name,
        "wallet_address": "",
        "api_key_provisioned": False,
        "cloud_client_ready": False,
        "erc8004_registered": False,
        "erc8004_tx": "",
    }

    wm = wallet_manager or WalletManager(config.conway.keystore_path)
    wallet = await wm.get_or_create_wallet(agent_name)
    result["wallet_address"] = wallet.address

    api_key = str(await get_config(f"conway_api_key_{agent_name}", "") or "").strip()
    if not api_key:
        api_key = str(config.conway.api_key or "").strip()

    if not api_key:
        try:
            api_key = (await provision_api_key_fn(wallet)).strip()
        except Exception as exc:
            logger.warning("SIWE provisioning failed for %s: %s", agent_name, exc)
            api_key = ""

        if api_key:
            await set_config(f"conway_api_key_{agent_name}", api_key)
            result["api_key_provisioned"] = True
            try:
                await emit_event(
                    "conway_api_key_provisioned",
                    {"agent": agent_name, "wallet": wallet.address},
                )
            except Exception:
                logger.debug("Failed to emit Conway API key provisioning event", exc_info=True)

    cloud_client = cloud_client_factory(
        api_key=api_key or None,
        base_url=config.conway.api_url,
        wallet=wallet,
        facilitator_url=config.conway.x402_facilitator_url,
    )
    result["cloud_client_ready"] = cloud_client.available

    registry_address = str(config.conway.erc8004_registry or "").strip()
    if agent_card and registry_address and registry_address != ZERO_ADDRESS:
        registry = registry_factory(contract_address=registry_address)
        try:
            already_registered = await registry.is_registered(wallet.address)
        except Exception as exc:
            logger.warning("ERC-8004 lookup failed for %s: %s", agent_name, exc)
            already_registered = False

        if already_registered:
            result["erc8004_registered"] = True
        else:
            try:
                tx_hash = (await registry.register(wallet, agent_card)).strip()
            except Exception as exc:
                logger.warning("ERC-8004 registration failed for %s: %s", agent_name, exc)
                tx_hash = ""

            if tx_hash:
                result["erc8004_registered"] = True
                result["erc8004_tx"] = tx_hash
                await set_config(f"conway_erc8004_tx_{agent_name}", tx_hash)
                try:
                    await emit_event(
                        "conway_agent_registered",
                        {
                            "agent": agent_name,
                            "wallet": wallet.address,
                            "tx_hash": tx_hash,
                        },
                    )
                except Exception:
                    logger.debug("Failed to emit Conway registration event", exc_info=True)

    return result


async def get_agent_cloud_client(
    agent_name: str,
    *,
    wallet_manager: WalletManager | None = None,
    provision_api_key_fn=provision_api_key,
    cloud_client_factory=ConwayCloudClient,
) -> ConwayCloudClient | None:
    """Return a Conway Cloud client bound to the agent wallet and API identity."""
    if not config.conway.enabled:
        return None

    wm = wallet_manager or WalletManager(config.conway.keystore_path)
    wallet = await wm.get_or_create_wallet(agent_name)

    api_key = str(await get_config(f"conway_api_key_{agent_name}", "") or "").strip()
    if not api_key:
        api_key = str(config.conway.api_key or "").strip()
    if not api_key:
        try:
            api_key = (await provision_api_key_fn(wallet)).strip()
        except Exception as exc:
            logger.warning("Cloud client SIWE provisioning failed for %s: %s", agent_name, exc)
            api_key = ""
        if api_key:
            await set_config(f"conway_api_key_{agent_name}", api_key)

    client = cloud_client_factory(
        api_key=api_key or None,
        base_url=config.conway.api_url,
        wallet=wallet,
        facilitator_url=config.conway.x402_facilitator_url,
    )
    return client if client.available else None


__all__ = ["ensure_agent_runtime", "get_agent_cloud_client"]
