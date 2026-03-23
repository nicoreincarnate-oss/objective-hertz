"""
SIWE (Sign-In With Ethereum) identity provisioning for Conway Cloud.

When an agent first connects to Conway Cloud, it authenticates via SIWE:
1. Request a nonce from Conway Cloud
2. Sign a SIWE message with the agent's wallet
3. Receive an API key for subsequent requests

This removes the need for human-managed API keys.
"""

import logging
import os
from typing import Any

import httpx

from conway.wallet import AgentWallet

logger = logging.getLogger("conway.identity")

DEFAULT_API_URL = "https://api.conway.tech"


async def provision_api_key(wallet: AgentWallet) -> str:
    """
    Provision a Conway Cloud API key via SIWE authentication.

    Returns the API key string, or empty string on failure.
    """
    base_url = os.environ.get("CONWAY_API_URL", DEFAULT_API_URL).rstrip("/")

    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Step 1: Request nonce
            nonce_resp = await client.get(f"{base_url}/v1/auth/nonce")
            nonce_resp.raise_for_status()
            nonce = nonce_resp.json().get("nonce", "")

            # Step 2: Build SIWE message
            siwe_message = (
                f"conway.tech wants you to sign in with your Ethereum account:\n"
                f"{wallet.address}\n\n"
                f"Sign in to Conway Cloud\n\n"
                f"URI: {base_url}\n"
                f"Version: 1\n"
                f"Chain ID: 8453\n"
                f"Nonce: {nonce}\n"
            )

            # Step 3: Sign message
            msg_hash = encode_defunct(text=siwe_message)
            signed = Account.sign_message(msg_hash, wallet._private_key)

            # Step 4: Verify and get API key
            verify_resp = await client.post(
                f"{base_url}/v1/auth/verify",
                json={
                    "message": siwe_message,
                    "signature": f"0x{signed.signature.hex()}",
                },
            )
            verify_resp.raise_for_status()
            data = verify_resp.json()
            api_key = data.get("apiKey", "")

            if api_key:
                logger.info(
                    f"Provisioned Conway Cloud API key for {wallet.agent_name} "
                    f"({wallet.address[:10]}...)"
                )
            return api_key

    except ImportError:
        logger.error("eth_account not installed for SIWE")
        return ""
    except Exception as e:
        logger.error(f"SIWE provisioning failed for {wallet.agent_name}: {e}")
        return ""
