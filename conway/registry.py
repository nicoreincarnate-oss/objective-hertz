"""
ERC-8004 Agent Registry on Base.

Registers Perseus agents on-chain so they are cryptographically verifiable
and discoverable by other agents (Conway ecosystem + beyond).

Standard: https://ethereum-magicians.org/t/erc-8004-autonomous-agent-identity/22268
"""

import json
import logging
import os
from typing import Any

import httpx

from conway.wallet import AgentWallet

logger = logging.getLogger("conway.registry")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _registry_contract() -> str:
    """Resolve the configured ERC-8004 registry contract address lazily."""
    configured = os.environ.get("ERC8004_REGISTRY_ADDRESS", "").strip()
    if configured:
        return configured
    try:
        from shared.config import config

        configured = str(config.conway.erc8004_registry or "").strip()
        if configured:
            return configured
    except Exception:
        pass
    return ZERO_ADDRESS


class AgentRegistry8004:
    """Register and discover agents on Base via ERC-8004."""

    def __init__(self, rpc_url: str | None = None, contract_address: str | None = None):
        self._rpc_url = rpc_url or os.environ.get(
            "BASE_RPC_URL", "https://mainnet.base.org"
        )
        self._contract_address = (contract_address or _registry_contract()).strip() or ZERO_ADDRESS

    async def register(
        self,
        wallet: AgentWallet,
        agent_card: dict[str, Any],
    ) -> str:
        """
        Register an agent on-chain via ERC-8004.

        agent_card should contain: name, description, capabilities, url
        Returns transaction hash or empty string on failure.
        """
        contract_address = self._contract_address or _registry_contract()
        if contract_address == ZERO_ADDRESS:
            logger.warning(
                "ERC-8004 registry address not configured. "
                "Set ERC8004_REGISTRY_ADDRESS env var."
            )
            return ""

        try:
            from eth_account import Account

            # Encode agent card as JSON and hash it
            card_json = json.dumps(agent_card, sort_keys=True)
            card_bytes = card_json.encode()

            # register(bytes agentCard) function signature
            # This is a simplified ABI call — actual implementation depends
            # on the deployed ERC-8004 contract interface
            register_selector = "0x1aa3a008"  # register(bytes)

            # ABI encode the bytes parameter
            offset = "0000000000000000000000000000000000000000000000000000000000000020"
            length = hex(len(card_bytes))[2:].zfill(64)
            # Pad card_bytes to 32-byte boundary
            padded_len = ((len(card_bytes) + 31) // 32) * 32
            padded_data = card_bytes.hex().ljust(padded_len * 2, "0")

            tx_data = f"{register_selector}{offset}{length}{padded_data}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get nonce
                nonce_resp = await client.post(
                    self._rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_getTransactionCount",
                        "params": [wallet.address, "latest"],
                        "id": 1,
                    },
                )
                nonce = int(nonce_resp.json()["result"], 16)

                # Get gas price
                gas_resp = await client.post(
                    self._rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_gasPrice",
                        "params": [],
                        "id": 1,
                    },
                )
                gas_price = int(gas_resp.json()["result"], 16)

                tx = {
                    "nonce": nonce,
                    "gasPrice": gas_price,
                    "gas": 200000,
                    "to": contract_address,
                    "value": 0,
                    "data": bytes.fromhex(tx_data[2:] if tx_data.startswith("0x") else tx_data),
                    "chainId": 8453,
                }

                signed = Account.sign_transaction(tx, wallet._private_key)
                raw_tx = "0x" + signed.raw_transaction.hex()

                send_resp = await client.post(
                    self._rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_sendRawTransaction",
                        "params": [raw_tx],
                        "id": 1,
                    },
                )
                result = send_resp.json()
                if "error" in result:
                    raise RuntimeError(result["error"].get("message", ""))

                tx_hash = result.get("result", "")
                logger.info(
                    f"Registered {wallet.agent_name} on Base via ERC-8004: {tx_hash[:16]}..."
                )
                return tx_hash

        except ImportError:
            logger.error("eth_account not installed")
            return ""
        except Exception as e:
            logger.error(f"ERC-8004 registration failed: {e}")
            return ""

    async def lookup(self, address: str) -> dict[str, Any] | None:
        """Look up an agent's card by their address."""
        contract_address = self._contract_address or _registry_contract()
        if contract_address == ZERO_ADDRESS:
            return None

        try:
            # getAgent(address) call
            lookup_selector = "0x5e01eb5a"
            padded_addr = address[2:].lower().zfill(64)
            data = f"{lookup_selector}{padded_addr}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self._rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_call",
                        "params": [
                            {"to": contract_address, "data": data},
                            "latest",
                        ],
                        "id": 1,
                    },
                )
                result = resp.json().get("result", "0x")
                if result == "0x" or len(result) <= 2:
                    return None

                # Decode ABI-encoded bytes response
                raw_bytes = bytes.fromhex(result[2:])
                # Skip ABI offset and length headers (64 bytes each)
                if len(raw_bytes) > 128:
                    card_json = raw_bytes[128:].rstrip(b"\x00").decode("utf-8", errors="ignore")
                    return json.loads(card_json)
                return None

        except Exception as e:
            logger.error(f"ERC-8004 lookup failed for {address}: {e}")
            return None

    async def is_registered(self, address: str) -> bool:
        """Check if an address is registered."""
        card = await self.lookup(address)
        return card is not None
