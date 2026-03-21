"""
Payment Router — routes invoices through Stripe (primary) or Wise (fallback).
Works with Mexican bank accounts per Perseus requirements.
"""

import hashlib
import logging
from typing import Any

import httpx

from shared.config import config
from tools.runtime_honesty import env_is_configured, truth_payload

logger = logging.getLogger("perseus.tools.payment")


class PaymentRouter:
    """Routes payment operations to Stripe or Wise based on availability."""

    def __init__(self):
        self._stripe_available = env_is_configured("STRIPE_API_KEY")
        self._wise_available = (
            env_is_configured("WISE_API_TOKEN") and env_is_configured("WISE_PROFILE_ID")
        )

    def get_status(self) -> dict[str, Any]:
        if self._stripe_available:
            return truth_payload("live", "stripe_api", True,
                                 summary="Stripe is configured and available.",
                                 provider="stripe")
        if self._wise_available:
            return truth_payload("live", "wise_api", True,
                                 summary="Wise is configured as payment fallback.",
                                 provider="wise")
        return truth_payload("blocked", "no_payment_provider", False,
                             summary="No payment provider configured. Set STRIPE_API_KEY or WISE_API_TOKEN.",
                             provider="payment")

    async def create_invoice(
        self,
        client_email: str,
        client_name: str,
        amount: float,
        description: str = "",
        currency: str = "USD",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        """Create an invoice/payment link. Tries Stripe first, then Wise."""
        invoice_key = idempotency_key or _invoice_idempotency_key(
            client_email, amount, description, currency
        )
        if self._stripe_available:
            return await self._create_stripe_invoice(
                client_email, client_name, amount, description, currency, invoice_key
            )
        if self._wise_available:
            return await self._create_wise_invoice(
                client_email, client_name, amount, description, currency, invoice_key
            )
        logger.warning("No payment provider available")
        return {"reference": "", "url": "", "provider": "none"}

    async def check_new_payments(self) -> list[dict[str, Any]]:
        """Check for new completed payments across all providers."""
        payments = []
        if self._stripe_available:
            payments.extend(await self._check_stripe_payments())
        if self._wise_available:
            payments.extend(await self._check_wise_payments())
        return payments

    # ── Stripe ──────────────────────────────────────────────────────

    async def _create_stripe_invoice(
        self,
        email: str,
        name: str,
        amount: float,
        description: str,
        currency: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a Stripe payment link for the invoice."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Create a payment link via Stripe API
                resp = await client.post(
                    "https://api.stripe.com/v1/payment_links",
                    headers={
                        "Authorization": f"Bearer {config.payment.stripe_api_key}",
                        "Idempotency-Key": idempotency_key,
                    },
                    data={
                        "line_items[0][price_data][currency]": currency.lower(),
                        "line_items[0][price_data][product_data][name]": description or "Website",
                        "line_items[0][price_data][unit_amount]": int(amount * 100),
                        "line_items[0][quantity]": 1,
                        "metadata[client_email]": email,
                        "metadata[client_name]": name,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "reference": data.get("id", ""),
                    "url": data.get("url", ""),
                    "provider": "stripe",
                }
        except Exception as e:
            logger.error(f"Stripe invoice creation failed: {e}")
            # Fall back to Wise if available
            if self._wise_available:
                return await self._create_wise_invoice(
                    email, name, amount, description, currency, idempotency_key
                )
            return {"reference": "", "url": "", "provider": "stripe", "error": str(e)}

    async def _check_stripe_payments(self) -> list[dict[str, Any]]:
        """Check Stripe for recent completed payments."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://api.stripe.com/v1/payment_intents",
                    headers={"Authorization": f"Bearer {config.payment.stripe_api_key}"},
                    params={"limit": 20, "created[gte]": _yesterday_timestamp()},
                )
                resp.raise_for_status()
                data = resp.json()
                payments = []
                for pi in data.get("data", []):
                    if pi.get("status") == "succeeded":
                        payments.append({
                            "reference": pi.get("id", ""),
                            "amount": pi.get("amount", 0) / 100,
                            "currency": pi.get("currency", "usd").upper(),
                            "provider": "stripe",
                            "metadata": pi.get("metadata", {}),
                        })
                return payments
        except Exception as e:
            logger.error(f"Stripe payment check failed: {e}")
            return []

    # ── Wise ────────────────────────────────────────────────────────

    async def _create_wise_invoice(
        self,
        email: str,
        name: str,
        amount: float,
        description: str,
        currency: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a Wise transfer quote (manual follow-up needed)."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Create a quote
                resp = await client.post(
                    "https://api.wise.com/v3/profiles/"
                    f"{config.payment.wise_profile_id}/quotes",
                    headers={
                        "Authorization": f"Bearer {config.payment.wise_api_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "sourceCurrency": currency,
                        "targetCurrency": currency,
                        "sourceAmount": amount,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "reference": data.get("id", ""),
                    "url": "",  # Wise doesn't have direct payment links
                    "provider": "wise",
                }
        except Exception as e:
            logger.error(f"Wise invoice creation failed: {e}")
            return {"reference": "", "url": "", "provider": "wise", "error": str(e)}

    async def _check_wise_payments(self) -> list[dict[str, Any]]:
        """Check Wise for recent incoming transfers."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "https://api.wise.com/v1/transfers",
                    headers={"Authorization": f"Bearer {config.payment.wise_api_token}"},
                    params={"profile": config.payment.wise_profile_id, "limit": 20},
                )
                resp.raise_for_status()
                transfers = resp.json()
                payments = []
                for t in (transfers if isinstance(transfers, list) else []):
                    if t.get("status") == "funds_converted":
                        payments.append({
                            "reference": str(t.get("id", "")),
                            "amount": t.get("targetValue", 0),
                            "currency": t.get("targetCurrency", "USD"),
                            "provider": "wise",
                        })
                return payments
        except Exception as e:
            logger.error(f"Wise payment check failed: {e}")
            return []


def _yesterday_timestamp() -> int:
    """Unix timestamp for 24 hours ago."""
    import time
    return int(time.time()) - 86400


def _invoice_idempotency_key(email: str, amount: float, description: str, currency: str) -> str:
    """Stable idempotency key for retried invoice creation."""
    raw = f"{email.strip().lower()}|{amount:.2f}|{currency.upper()}|{description.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()
