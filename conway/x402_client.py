"""
x402 payment protocol client for agent-to-service payments.

Implements the x402 HTTP payment protocol (Coinbase, 2025) which allows
AI agents to pay for web services using USDC on Base.

Protocol flow:
1. Client makes HTTP request to service
2. Service returns 402 Payment Required with payment details
3. Client constructs payment payload (EVM transfer)
4. Client retries request with X-PAYMENT header
5. Facilitator verifies and settles payment

Reference: tools/firecrawl/apps/api/src/lib/x402.ts (TypeScript implementation)
"""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from conway.wallet import AgentWallet

logger = logging.getLogger("conway.x402")

DEFAULT_FACILITATOR = "https://x402.org/facilitator"
BASE_CHAIN_ID = "eip155:8453"


@dataclass
class PaymentRequirement:
    """Payment details returned by a 402 response."""

    scheme: str  # "exact"
    network: str  # "eip155:8453"
    price: str  # "$0.01" or "0.01"
    pay_to: str  # 0x... address
    description: str
    resource_url: str


class X402Client:
    """x402 payment protocol client for USDC payments on Base."""

    def __init__(self, facilitator_url: str = DEFAULT_FACILITATOR):
        self._facilitator_url = facilitator_url

    async def check_payment_required(
        self, url: str
    ) -> PaymentRequirement | None:
        """Check if a URL requires x402 payment. Returns requirement or None."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code != 402:
                    return None

                # Parse 402 response for payment details
                # x402 returns payment info in the response body
                try:
                    body = resp.json()
                except Exception:
                    return None

                accepts = body.get("accepts", {})
                if not accepts:
                    return None

                return PaymentRequirement(
                    scheme=accepts.get("scheme", "exact"),
                    network=accepts.get("network", BASE_CHAIN_ID),
                    price=accepts.get("price", "0"),
                    pay_to=accepts.get("payTo", ""),
                    description=body.get("description", ""),
                    resource_url=url,
                )
        except Exception as e:
            logger.error(f"x402 check failed for {url}: {e}")
            return None

    async def pay_and_request(
        self,
        url: str,
        wallet: AgentWallet,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make an x402 payment and access the resource.

        1. Check if URL requires payment
        2. Construct payment via facilitator
        3. Send request with X-PAYMENT header
        """
        # Step 1: Check for 402
        requirement = await self.check_payment_required(url)
        if requirement is None:
            # No payment required — just make the request
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(method, url, json=body)
                return {"status": resp.status_code, "data": resp.json()}

        # Step 2: Create payment payload via facilitator
        payment_header = await self._create_payment(wallet, requirement)
        if not payment_header:
            return {"status": 402, "error": "Failed to create payment"}

        # Step 3: Retry with payment header
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"X-PAYMENT": payment_header}
            resp = await client.request(method, url, headers=headers, json=body)
            if resp.status_code == 200:
                logger.info(
                    f"x402 payment succeeded: {wallet.agent_name} paid "
                    f"{requirement.price} to {requirement.pay_to[:10]}..."
                )
            return {"status": resp.status_code, "data": resp.json()}

    async def _create_payment(
        self, wallet: AgentWallet, requirement: PaymentRequirement
    ) -> str:
        """Create an x402 payment payload via the facilitator."""
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct

            # Parse price (remove $ prefix if present)
            price_str = requirement.price.lstrip("$")
            price = Decimal(price_str)

            # Create the payment message
            payment_payload = {
                "scheme": requirement.scheme,
                "network": requirement.network,
                "price": str(price),
                "payTo": requirement.pay_to,
                "payer": wallet.address,
                "resource": requirement.resource_url,
            }

            # Sign the payment intent
            message = json.dumps(payment_payload, sort_keys=True)
            msg_hash = encode_defunct(text=message)
            signed = Account.sign_message(msg_hash, wallet._private_key)
            signature = signed.signature.hex()

            # Verify with facilitator
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._facilitator_url}/verify",
                    json={
                        "payload": payment_payload,
                        "signature": f"0x{signature}",
                    },
                )
                if resp.status_code == 200:
                    result = resp.json()
                    return result.get("paymentHeader", "")
                else:
                    logger.error(f"Facilitator rejected payment: {resp.text}")
                    return ""

        except ImportError:
            logger.error("eth_account not installed for x402 signing")
            return ""
        except Exception as e:
            logger.error(f"x402 payment creation failed: {e}")
            return ""
