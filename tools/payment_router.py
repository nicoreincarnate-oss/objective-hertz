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
    """Routes payment operations to Stripe, Wise, or Conway (x402/USDC) based on availability."""

    def __init__(self):
        self._stripe_available = env_is_configured("STRIPE_API_KEY")
        self._wise_available = (
            env_is_configured("WISE_API_TOKEN") and env_is_configured("WISE_PROFILE_ID")
        )
        self._conway_available = env_is_configured("CONWAY_ENABLED") and env_is_configured("CONWAY_TREASURY_ADDRESS")

    def get_status(self) -> dict[str, Any]:
        if self._stripe_available:
            return truth_payload("live", "stripe_api", True,
                                 summary="Stripe is configured and available.",
                                 provider="stripe")
        if self._wise_available:
            return truth_payload("live", "wise_api", True,
                                 summary="Wise is configured as payment fallback.",
                                 provider="wise")
        if self._conway_available:
            return truth_payload("live", "conway_x402", True,
                                 summary="Conway x402 (USDC on Base) is available.",
                                 provider="conway")
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

    async def check_new_payments(self, since_timestamp: int = 0) -> dict[str, Any]:
        """Check for new completed payments across all providers.

        ``since_timestamp`` is a Unix epoch.  When non-zero the polling
        window starts there instead of a fixed 24-hour lookback, which
        prevents payments from falling out of the window during daemon
        downtime.  The caller is responsible for persisting this cursor.

        Returns:
            {"payments": [...], "exhausted": bool}
            ``exhausted`` is True only if every provider's result set was
            fully consumed (no page cap hit).  When False, the caller must
            NOT advance the time cursor — unseen payments remain behind it.
        """
        if not since_timestamp:
            since_timestamp = _default_lookback_timestamp()
        payments: list[dict[str, Any]] = []
        all_exhausted = True
        if self._stripe_available:
            stripe = await self._check_stripe_payments(since_timestamp)
            payments.extend(stripe["payments"])
            if not stripe["exhausted"]:
                all_exhausted = False
        if self._wise_available:
            wise = await self._check_wise_payments(since_timestamp)
            payments.extend(wise["payments"])
            if not wise["exhausted"]:
                all_exhausted = False
        if self._conway_available:
            payments.extend(await self._check_conway_payments())
        return {"payments": payments, "exhausted": all_exhausted}

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
                # Include idempotency_key as reconciliation anchor in metadata.
                # Stripe payment links, checkout sessions, and payment intents
                # are different objects with different IDs. We store a stable
                # key in metadata so payment checks can match back to our deal.
                resp = await client.post(
                    "https://api.stripe.com/v1/payment_links",
                    headers={
                        "Authorization": f"Bearer {config.payment.stripe_api_key}",
                        "Idempotency-Key": idempotency_key,
                    },
                    data={
                        "line_items[0][price_data][currency]": currency.lower(),
                        "line_items[0][price_data][product_data][name]": description or "Website",
                        "line_items[0][price_data][unit_amount]": int(round(float(amount) * 100)),
                        "line_items[0][quantity]": 1,
                        # Payment Link metadata (for our own records)
                        "metadata[client_email]": email,
                        "metadata[client_name]": name,
                        "metadata[perseus_key]": idempotency_key,
                        # CRITICAL: payment_intent_data.metadata propagates to
                        # the PaymentIntent that Stripe creates when the customer
                        # pays.  Without this, _check_stripe_payments cannot
                        # reconcile because it reads PI metadata, not PL metadata.
                        "payment_intent_data[metadata][perseus_key]": idempotency_key,
                        "payment_intent_data[metadata][client_email]": email,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "reference": idempotency_key,
                    "payment_link_id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "provider": "stripe",
                }
        except Exception as e:
            logger.error(f"Stripe invoice creation failed: {e}")
            # Retry the same Stripe request with the same Idempotency-Key.
            # Stripe guarantees: if the original succeeded server-side, the
            # retry returns the original response without creating a duplicate.
            stripe_result = await self._retry_stripe_idempotent(
                email, name, amount, description, currency, idempotency_key,
            )
            if stripe_result:
                logger.info("Stripe idempotent retry succeeded — not falling back to Wise")
                return stripe_result
            # FAIL-CLOSED: if the retry also failed, we cannot prove Stripe
            # didn't already create the payment link. Creating a Wise
            # artifact would risk a duplicate payable invoice. Return an
            # error requiring manual intervention instead of silently
            # minting a second artifact.
            logger.error(
                "Stripe creation and idempotent retry both failed for %s — "
                "refusing Wise fallback to avoid duplicate artifact",
                idempotency_key,
            )
            return {"reference": "", "url": "", "provider": "stripe", "error": f"ambiguous Stripe failure: {e}"}

    async def _retry_stripe_idempotent(
        self,
        email: str,
        name: str,
        amount: float,
        description: str,
        currency: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Retry the Stripe payment link creation with the same Idempotency-Key.

        Stripe guarantees: if the original request succeeded server-side,
        replaying the same key returns the original response without
        creating a duplicate. If it truly failed, the retry will either
        succeed (creating the link now) or fail again (confirming no link).
        This is more reliable than searching recent links by metadata.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.stripe.com/v1/payment_links",
                    headers={
                        "Authorization": f"Bearer {config.payment.stripe_api_key}",
                        "Idempotency-Key": idempotency_key,
                    },
                    data={
                        "line_items[0][price_data][currency]": currency.lower(),
                        "line_items[0][price_data][product_data][name]": description or "Website",
                        "line_items[0][price_data][unit_amount]": int(round(float(amount) * 100)),
                        "line_items[0][quantity]": 1,
                        "metadata[client_email]": email,
                        "metadata[client_name]": name,
                        "metadata[perseus_key]": idempotency_key,
                        "payment_intent_data[metadata][perseus_key]": idempotency_key,
                        "payment_intent_data[metadata][client_email]": email,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "reference": idempotency_key,
                    "payment_link_id": data.get("id", ""),
                    "url": data.get("url", ""),
                    "provider": "stripe",
                }
        except Exception as e:
            logger.debug(f"Stripe idempotent retry also failed (will fall back to Wise): {e}")
            return None

    async def _check_stripe_payments(self, since_timestamp: int = 0) -> dict[str, Any]:
        """Check Stripe for completed payments, paginating to avoid missing any.

        Returns {"payments": [...], "exhausted": bool}.
        ``exhausted`` is False if the page cap was hit and there may be more.
        """
        if not since_timestamp:
            since_timestamp = _default_lookback_timestamp()
        payments: list[dict[str, Any]] = []
        exhausted = True
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                has_more = True
                starting_after: str | None = None
                page = 0
                max_pages = 10  # safety cap: 1000 PIs max
                while has_more and page < max_pages:
                    params: dict[str, Any] = {
                        "limit": 100,
                        "created[gte]": since_timestamp,
                    }
                    if starting_after:
                        params["starting_after"] = starting_after
                    resp = await client.get(
                        "https://api.stripe.com/v1/payment_intents",
                        headers={"Authorization": f"Bearer {config.payment.stripe_api_key}"},
                        params=params,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    for pi in data.get("data", []):
                        if pi.get("status") == "succeeded":
                            pi_meta = pi.get("metadata", {})
                            ref = pi_meta.get("perseus_key", "")
                            if not ref:
                                ref = pi.get("id", "")
                            payments.append({
                                "reference": ref,
                                "amount": pi.get("amount", 0) / 100,
                                "currency": pi.get("currency", "usd").upper(),
                                "provider": "stripe",
                                "metadata": pi_meta,
                            })
                    has_more = data.get("has_more", False)
                    items = data.get("data", [])
                    starting_after = items[-1]["id"] if items else None
                    page += 1
                # If we exited the loop because of the page cap while
                # has_more was still True, the result set was NOT exhausted.
                if has_more:
                    exhausted = False
                    logger.warning(
                        "Stripe polling hit page cap (%d pages, %d PIs) — cursor will NOT advance",
                        max_pages, len(payments),
                    )
                return {"payments": payments, "exhausted": exhausted}
        except Exception as e:
            logger.error(f"Stripe payment check failed: {e}")
            return {"payments": [], "exhausted": False}

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
        """Create a Wise quote and transfer with reconciliation key.

        The transfer is created with ``customerTransactionId`` set to our
        idempotency key so that ``_check_wise_payments`` can match the
        completed transfer back to the deal.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Step 1: Create a quote
                quote_resp = await client.post(
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
                quote_resp.raise_for_status()
                quote_data = quote_resp.json()
                quote_id = str(quote_data.get("id", ""))

                # Step 2: Create a transfer from the quote.
                # customerTransactionId is the field _check_wise_payments
                # matches on — this closes the reconciliation loop.
                transfer_resp = await client.post(
                    "https://api.wise.com/v1/transfers",
                    headers={
                        "Authorization": f"Bearer {config.payment.wise_api_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "targetAccount": config.payment.wise_profile_id,
                        "quoteUuid": quote_id,
                        "customerTransactionId": idempotency_key,
                        "details": {
                            "reference": idempotency_key,
                            "sourceOfFunds": "verification.source.of.funds.other",
                        },
                    },
                )
                transfer_resp.raise_for_status()
                transfer_data = transfer_resp.json()

                transfer_id = str(transfer_data.get("id", ""))
                return {
                    "reference": idempotency_key,
                    "quote_id": quote_id,
                    "transfer_id": transfer_id,
                    "url": "",  # Wise has no direct payment links
                    "provider": "wise",
                    "payment_instructions": (
                        f"Bank transfer reference: {idempotency_key}\n"
                        f"Wise transfer ID: {transfer_id}\n"
                        f"Amount: {amount} {currency}\n"
                        f"Please include the reference in your transfer."
                    ),
                }
        except Exception as e:
            logger.error(f"Wise invoice creation failed: {e}")
            return {"reference": "", "url": "", "provider": "wise", "error": str(e)}

    async def _check_wise_payments(self, since_timestamp: int = 0) -> dict[str, Any]:
        """Check Wise for incoming transfers, paginating with offset.

        Returns {"payments": [...], "exhausted": bool}.
        ``exhausted`` is False if the page cap was hit and there may be more.
        """
        import datetime

        if not since_timestamp:
            since_timestamp = _default_lookback_timestamp()
        since_date = datetime.datetime.fromtimestamp(since_timestamp, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

        payments: list[dict[str, Any]] = []
        exhausted = True
        max_pages = 5  # safety cap: 500 transfers max
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                offset = 0
                page = 0
                while page < max_pages:
                    resp = await client.get(
                        "https://api.wise.com/v1/transfers",
                        headers={"Authorization": f"Bearer {config.payment.wise_api_token}"},
                        params={
                            "profile": config.payment.wise_profile_id,
                            "limit": 100,
                            "offset": offset,
                            "createdDateStart": since_date,
                        },
                    )
                    resp.raise_for_status()
                    transfers = resp.json()
                    batch = transfers if isinstance(transfers, list) else []
                    for t in batch:
                        if t.get("status") == "funds_converted":
                            ref = (
                                t.get("customerTransactionId", "")
                                or t.get("reference", "")
                                or str(t.get("id", ""))
                            )
                            payments.append({
                                "reference": ref,
                                "amount": t.get("targetValue", 0),
                                "currency": t.get("targetCurrency", "USD"),
                                "provider": "wise",
                                "transfer_id": str(t.get("id", "")),
                            })
                    if len(batch) < 100:
                        break  # last page — fully exhausted
                    offset += 100
                    page += 1
                else:
                    # Exited via page cap, not break — more data may exist
                    exhausted = False
                    logger.warning(
                        "Wise polling hit page cap (%d pages, %d transfers) — cursor will NOT advance",
                        max_pages, len(payments),
                    )
                return {"payments": payments, "exhausted": exhausted}
        except Exception as e:
            logger.error(f"Wise payment check failed: {e}")
            return {"payments": [], "exhausted": False}


    # ── Conway (x402 / USDC on Base) ──────────────────────────────

    async def _check_conway_payments(self) -> list[dict[str, Any]]:
        """Check Conway ledger for recent incoming USDC payments."""
        try:
            from conway.ledger import EconomicLedger
            ledger = EconomicLedger()
            txs = await ledger.recent_transactions(limit=20)
            payments = []
            for tx in txs:
                if tx.get("tx_type") == "earn":
                    payments.append({
                        "reference": tx.get("tx_hash", ""),
                        "amount": float(tx.get("amount", 0)),
                        "currency": tx.get("currency", "USDC"),
                        "provider": "conway",
                        "metadata": {"agent": tx.get("agent", "")},
                    })
            return payments
        except Exception as e:
            logger.error(f"Conway payment check failed: {e}")
            return []


def _yesterday_timestamp() -> int:
    """Unix timestamp for 24 hours ago."""
    import time
    return int(time.time()) - 86400


def _default_lookback_timestamp() -> int:
    """Default lookback: 7 days.  Wider than 24h so daemon downtime
    doesn't permanently lose payments from the polling window."""
    import time
    return int(time.time()) - 7 * 86400


def _invoice_idempotency_key(email: str, amount: float, description: str, currency: str) -> str:
    """Stable idempotency key for retried invoice creation."""
    raw = f"{email.strip().lower()}|{amount:.2f}|{currency.upper()}|{description.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()
