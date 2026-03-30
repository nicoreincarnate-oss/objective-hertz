"""
Wallet management for Perseus agents on Base L2.

Each agent gets an Ethereum wallet that holds USDC on Base.
Wallets are persisted as encrypted keystores on disk and
registered in the conway_wallets DB table.

PQC Integration:
  When PQC_ENABLED=true, wallet operations use HybridEncryptor for
  keystore encryption and QuantumSafeSigner for transaction signing.
  Falls back to classical crypto when PQC imports fail.
"""

import json
import logging
import os
from decimal import Decimal
from pathlib import Path

import httpx

from shared.db import execute, fetch_one

logger = logging.getLogger("conway.wallet")

# --- PQC integration (graceful fallback) ---
_PQC_AVAILABLE = False
try:
    from conway.pqc import (
        HybridEncryptor,
        QuantumSafeSigner,
        derive_agent_key,
        is_pqc_enabled,
    )

    _PQC_AVAILABLE = True
except ImportError:
    logger.warning("PQC module unavailable — wallet uses classical crypto only")

    def is_pqc_enabled() -> bool:  # type: ignore[misc]
        """Stub: PQC always disabled when module unavailable."""
        return False

# Base mainnet USDC contract (Circle)
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# ERC-20 balanceOf(address) selector
BALANCE_OF_SELECTOR = "0x70a08231"
# ERC-20 transfer(address,uint256) selector
TRANSFER_SELECTOR = "0xa9059cbb"
# USDC has 6 decimals
USDC_DECIMALS = 6


class AgentWallet:
    """Ethereum wallet for a single Perseus agent on Base L2.

    When PQC_ENABLED=true, transaction signing uses QuantumSafeSigner
    (Ed25519, upgradeable to ML-DSA-65) in addition to Ethereum signing.
    """

    def __init__(self, agent_name: str, address: str, private_key: str):
        if not isinstance(private_key, str) or not private_key.startswith("0x") or len(private_key) != 66:
            raise ValueError(
                "private_key must be a 66-character hex string starting with '0x' "
                "(e.g. '0x' + 64 hex digits)"
            )
        self.agent_name = agent_name
        self._address = address
        self._private_key = private_key
        self._pqc_signer: QuantumSafeSigner | None = None

        # Initialize PQC signer when enabled
        if _PQC_AVAILABLE and is_pqc_enabled():
            try:
                master_pw = os.environ.get("CONWAY_KEYSTORE_PASSWORD", "")
                if master_pw:
                    agent_key = derive_agent_key(agent_name, master_pw)
                    # Use first 32 bytes of agent key as signing seed
                    self._pqc_signer = QuantumSafeSigner(agent_key)
                    logger.info(f"PQC signer initialized for {agent_name}")
            except Exception as e:
                logger.warning(f"PQC signer init failed for {agent_name}: {e}")

    @property
    def address(self) -> str:
        return self._address

    @property
    def pqc_signer(self) -> QuantumSafeSigner | None:
        """Return the PQC signer if PQC is enabled, else None."""
        return self._pqc_signer

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

            # PQC: sign transaction payload for quantum-safe audit trail
            pqc_signature: bytes | None = None
            if self._pqc_signer is not None:
                try:
                    pqc_signature = self._pqc_signer.sign(tx_data.encode("utf-8"))
                    logger.info(
                        f"PQC signature attached for {self.agent_name} tx "
                        f"(sig={pqc_signature[:8].hex()}...)"
                    )
                except Exception as e:
                    logger.warning(f"PQC signing failed (non-fatal): {e}")

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
        """Generate a new Ethereum wallet and save encrypted keystore.

        When PQC_ENABLED=true, also encrypts the keystore with HybridEncryptor
        using per-agent derived key for quantum-safe protection at rest.
        """
        try:
            from eth_account import Account

            acct = Account.create()
            address = acct.address
            private_key = acct.key.hex()

            keystore_path = self._keystore_dir / f"{agent_name}.json"
            encrypted = Account.encrypt(private_key, _get_keystore_password())
            keystore_json = json.dumps(encrypted)

            # PQC: wrap keystore with hybrid encryption
            if _PQC_AVAILABLE and is_pqc_enabled():
                try:
                    agent_key = derive_agent_key(
                        agent_name, _get_keystore_password()
                    )
                    encryptor = HybridEncryptor(agent_key)
                    pqc_ct = encryptor.encrypt(keystore_json.encode("utf-8"))
                    # Write PQC-wrapped keystore with marker
                    wrapped = {
                        "pqc_wrapped": True,
                        "ciphertext": pqc_ct.hex(),
                    }
                    keystore_path.write_text(json.dumps(wrapped))
                    logger.info(f"PQC-encrypted keystore for {agent_name}")
                except Exception as e:
                    logger.warning(
                        f"PQC keystore encryption failed, using classical: {e}"
                    )
                    keystore_path.write_text(keystore_json)
            else:
                keystore_path.write_text(keystore_json)

            return AgentWallet(agent_name, address, private_key)
        except ImportError:
            logger.error("eth_account not installed. Run: pip install eth-account")
            raise

    def _load_from_keystore(
        self, agent_name: str, address: str, keystore_ref: str
    ) -> AgentWallet:
        """Load wallet from encrypted keystore file.

        Handles both PQC-wrapped and classical keystores transparently.
        """
        try:
            from eth_account import Account

            keystore_path = self._keystore_dir / keystore_ref
            if not keystore_path.exists():
                logger.warning(
                    f"Keystore {keystore_ref} not found for {agent_name}, creating new"
                )
                return self._create_new_wallet(agent_name)

            raw = json.loads(keystore_path.read_text())

            # Check for PQC-wrapped keystore
            if isinstance(raw, dict) and raw.get("pqc_wrapped"):
                if not _PQC_AVAILABLE:
                    raise RuntimeError(
                        f"Keystore for {agent_name} is PQC-encrypted but PQC module unavailable"
                    )
                agent_key = derive_agent_key(
                    agent_name, _get_keystore_password()
                )
                encryptor = HybridEncryptor(agent_key)
                plaintext = encryptor.decrypt(bytes.fromhex(raw["ciphertext"]))
                encrypted = json.loads(plaintext.decode("utf-8"))
            else:
                encrypted = raw

            private_key = Account.decrypt(encrypted, _get_keystore_password())
            return AgentWallet(agent_name, address, private_key.hex())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Corrupt keystore file {keystore_ref}: {e}")
            raise RuntimeError(f"Cannot read keystore {keystore_ref}: {e}") from e
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
