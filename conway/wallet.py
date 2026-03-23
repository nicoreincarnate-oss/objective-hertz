"""
Wallet management for Perseus agents on Base L2.

Each agent gets an Ethereum wallet that holds USDC on Base.
Wallets are persisted as encrypted keystores on disk and
registered in the conway_wallets DB table.
"""

import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from shared.config import config
from shared.db import execute, fetch_one

logger = logging.getLogger("conway.wallet")

# Base mainnet USDC contract (Circle)
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# ERC-20 balanceOf(address) selector
BALANCE_OF_SELECTOR = "0x70a08231"
# ERC-20 transfer(address,uint256) selector
TRANSFER_SELECTOR = "0xa9059cbb"
# USDC has 6 decimals
USDC_DECIMALS = 6


class AgentWallet:
    """Ethereum wallet for a single Perseus agent on Base L2."""

    def __init__(self, agent_name: str, address: str, private_key: str):
        self.agent_name = agent_name
        self._address = address
        self._private_key = private_key

    @property
    def address(self) -> str:
        return self._address

    async def get_balance(self) -> Decimal:
        """Get USDC balance on Base."""
        rpc_url = _get_rpc_url()
        # Encode balanceOf(address) call
        padded_addr = self._address[2:].lower().zfill(64)
        data = f"{BALANCE_OF_SELECTOR}{padded_addr}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_call",
                        "params": [
                            {"to": USDC_CONTRACT, "data": data},
                            "latest",
                        ],
                        "id": 1,
                    },
                )
                resp.raise_for_status()
                result = resp.json().get("result", "0x0")
                raw_balance = int(result, 16)
                return Decimal(raw_balance) / Decimal(10**USDC_DECIMALS)
        except Exception as e:
            logger.error(f"Failed to get balance for {self.agent_name}: {e}")
            return Decimal("0")

    async def send_usdc(
        self, to_address: str, amount: Decimal, memo: str = ""
    ) -> str:
        """Send USDC to an address on Base. Returns tx hash."""
        try:
            from eth_account import Account

            rpc_url = _get_rpc_url()
            raw_amount = int(amount * Decimal(10**USDC_DECIMALS))

            # Encode transfer(address, uint256)
            padded_to = to_address[2:].lower().zfill(64)
            padded_amount = hex(raw_amount)[2:].zfill(64)
            tx_data = f"{TRANSFER_SELECTOR}{padded_to}{padded_amount}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get nonce
                nonce_resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_getTransactionCount",
                        "params": [self._address, "latest"],
                        "id": 1,
                    },
                )
                nonce = int(nonce_resp.json()["result"], 16)

                # Get gas price
                gas_resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_gasPrice",
                        "params": [],
                        "id": 1,
                    },
                )
                gas_price = int(gas_resp.json()["result"], 16)

                # Build transaction
                tx = {
                    "nonce": nonce,
                    "gasPrice": gas_price,
                    "gas": 65000,  # ERC-20 transfer typical gas
                    "to": USDC_CONTRACT,
                    "value": 0,
                    "data": bytes.fromhex(tx_data[2:] if tx_data.startswith("0x") else tx_data),
                    "chainId": 8453,  # Base mainnet
                }

                # Sign and send
                signed = Account.sign_transaction(tx, self._private_key)
                raw_tx = "0x" + signed.raw_transaction.hex()

                send_resp = await client.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_sendRawTransaction",
                        "params": [raw_tx],
                        "id": 1,
                    },
                )
                result = send_resp.json()
                if "error" in result:
                    raise RuntimeError(result["error"].get("message", str(result["error"])))

                tx_hash = result.get("result", "")
                logger.info(
                    f"USDC sent: {self.agent_name} → {to_address[:10]}... "
                    f"amount={amount} USDC tx={tx_hash[:16]}..."
                )
                return tx_hash
        except ImportError:
            logger.error("eth_account not installed. Run: pip install eth-account")
            return ""
        except Exception as e:
            logger.error(f"USDC send failed for {self.agent_name}: {e}")
            return ""


class WalletManager:
    """Manages wallets for all Perseus agents + treasury."""

    def __init__(self, keystore_dir: str | None = None):
        self._keystore_dir = Path(
            keystore_dir
            or os.environ.get("CONWAY_KEYSTORE_PATH", "conway/data/keystores")
        )
        self._keystore_dir.mkdir(parents=True, exist_ok=True)
        self._wallets: dict[str, AgentWallet] = {}

    async def get_or_create_wallet(self, agent_name: str) -> AgentWallet:
        """Get existing wallet or create a new one for an agent."""
        if agent_name in self._wallets:
            return self._wallets[agent_name]

        # Check DB first
        row = await fetch_one(
            "SELECT public_address, keystore_ref FROM conway_wallets WHERE agent_name = %s",
            (agent_name,),
        )
        if row:
            wallet = self._load_from_keystore(
                agent_name, row["public_address"], row["keystore_ref"]
            )
            self._wallets[agent_name] = wallet
            return wallet

        # Create new wallet
        wallet = self._create_new_wallet(agent_name)
        self._wallets[agent_name] = wallet

        # Persist to DB
        await execute(
            """INSERT INTO conway_wallets (agent_name, chain, public_address, keystore_ref)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (agent_name) DO NOTHING""",
            (agent_name, "base", wallet.address, f"{agent_name}.json"),
        )

        logger.info(f"Created wallet for {agent_name}: {wallet.address}")
        return wallet

    async def fund_agent(
        self, agent_name: str, amount: Decimal, source: str = "treasury"
    ) -> str:
        """Fund an agent's wallet from the treasury. Returns tx hash."""
        treasury_key = os.environ.get("CONWAY_TREASURY_PRIVATE_KEY", "")
        treasury_addr = os.environ.get("CONWAY_TREASURY_ADDRESS", "")
        if not treasury_key or not treasury_addr:
            logger.error("Treasury wallet not configured")
            return ""

        treasury = AgentWallet("treasury", treasury_addr, treasury_key)
        agent_wallet = await self.get_or_create_wallet(agent_name)
        return await treasury.send_usdc(agent_wallet.address, amount, f"fund:{agent_name}")

    async def all_balances(self) -> dict[str, Decimal]:
        """Get USDC balances for all registered agent wallets."""
        from shared.db import fetch_all

        rows = await fetch_all("SELECT agent_name, public_address FROM conway_wallets")
        balances = {}
        for row in rows or []:
            wallet = await self.get_or_create_wallet(row["agent_name"])
            balances[row["agent_name"]] = await wallet.get_balance()
        return balances

    def _create_new_wallet(self, agent_name: str) -> AgentWallet:
        """Generate a new Ethereum wallet and save encrypted keystore."""
        try:
            from eth_account import Account

            acct = Account.create()
            address = acct.address
            private_key = acct.key.hex()

            keystore_path = self._keystore_dir / f"{agent_name}.json"
            encrypted = Account.encrypt(private_key, _get_keystore_password())
            keystore_path.write_text(json.dumps(encrypted))

            return AgentWallet(agent_name, address, private_key)
        except ImportError:
            logger.error("eth_account not installed. Run: pip install eth-account")
            raise

    def _load_from_keystore(
        self, agent_name: str, address: str, keystore_ref: str
    ) -> AgentWallet:
        """Load wallet from encrypted keystore file."""
        try:
            from eth_account import Account

            keystore_path = self._keystore_dir / keystore_ref
            if not keystore_path.exists():
                logger.warning(
                    f"Keystore {keystore_ref} not found for {agent_name}, creating new"
                )
                return self._create_new_wallet(agent_name)

            encrypted = json.loads(keystore_path.read_text())
            private_key = Account.decrypt(encrypted, _get_keystore_password())
            return AgentWallet(agent_name, address, private_key.hex())
        except ImportError:
            logger.error("eth_account not installed")
            raise


def _get_keystore_password() -> str:
    """Get keystore encryption password from env. Never fall back to a guessable default."""
    pw = os.environ.get("CONWAY_KEYSTORE_PASSWORD", "")
    if not pw:
        raise RuntimeError(
            "CONWAY_KEYSTORE_PASSWORD env var is required for wallet operations. "
            "Set a strong password to encrypt agent keystores."
        )
    return pw


def _get_rpc_url() -> str:
    """Get Base RPC URL from config."""
    return os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
