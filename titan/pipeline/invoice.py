"""
Stage 10: Invoice & Payment
Send invoices and track payments. Works with Mexican bank account.
"""

import logging

from shared.config import config
from shared.db import emit_event, execute, fetch_all, fetch_one, get_config, set_config
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.invoice")


async def process_invoices():
    """Send invoices for deployed sites and check for payments."""
    if await get_config("shadow_mode", False):
        logger.info("SHADOW: invoice stage skipped entirely")
        return
    await _send_invoices()
    await _check_payments()


async def _send_invoices():
    """Send invoices for deployed sites that haven't been invoiced."""
    leads = await fetch_all(
        """SELECT c.id, c.business_name, c.contact_name, c.email, c.language
           FROM clients c
           WHERE c.status = 'deployed'
           ORDER BY c.created_at ASC LIMIT 5"""
    )

    for lead in leads:
        try:
            result = await _create_and_send_invoice(lead)
            if not result:
                continue
            deal_id = result["deal_id"]
            delivered = result["delivered"]

            if delivered:
                await transition_lead(lead["id"], "invoiced")
                await emit_event("invoice_sent", {
                    "client_id": lead["id"],
                    "business_name": lead["business_name"],
                    "deal_id": deal_id,
                })
                logger.info(f"Invoice sent to {lead['business_name']}")
            else:
                # Payment artifact was created but delivery to the client
                # failed.  Do NOT transition to 'invoiced' or emit
                # 'invoice_sent' — that would be lying about a revenue step.
                await emit_event("invoice_created_not_delivered", {
                    "client_id": lead["id"],
                    "business_name": lead["business_name"],
                    "deal_id": deal_id,
                    "reason": result.get("delivery_error", "delivery not attempted"),
                })
                logger.warning(
                    "Invoice created but NOT delivered for %s (deal %s) — needs manual send",
                    lead["business_name"], deal_id,
                )
        except Exception as e:
            logger.error(f"Invoice failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("invoice", e, lead_id=lead["id"])


async def _create_and_send_invoice(lead: dict) -> dict | None:
    """Create an invoice and attempt to deliver it to the client.

    Returns a dict with:
      - deal_id: int  (the persisted deal)
      - delivered: bool  (whether the invoice was actually sent to the client)
      - delivery_error: str  (if delivered is False)
    Returns None if no payment artifact could be created at all.
    """
    amount = config.pricing.website_5page  # Default 5-page price
    description = f"Professional 5-page website for {lead['business_name']}"

    existing_deal = await fetch_one(
        """SELECT id, wise_reference, payment_url, payment_provider, payment_instructions
           FROM deals
           WHERE client_id = %s AND product = 'website' AND status = 'pending'
           ORDER BY created_at DESC LIMIT 1""",
        (lead["id"],),
    )
    if existing_deal and existing_deal.get("wise_reference"):
        logger.info(
            "Reusing existing pending invoice for %s: %s",
            lead["business_name"],
            existing_deal["wise_reference"],
        )
        # Still need to deliver if it was created but never sent
        delivered, delivery_error = await _deliver_invoice(
            lead, existing_deal.get("payment_url", ""), existing_deal.get("wise_reference", ""),
            existing_deal.get("payment_instructions", ""),
        )
        return {"deal_id": existing_deal["id"], "delivered": delivered, "delivery_error": delivery_error}

    # Create via payment platform first — don't create a deal record until we have a deliverable invoice
    try:
        from tools.payment_router import PaymentRouter
        router = PaymentRouter()
        result = await router.create_invoice(
            client_email=lead["email"],
            client_name=lead.get("contact_name", lead["business_name"]),
            amount=amount,
            description=description,
            idempotency_key=f"website:{lead['id']}:{amount}",
        )
    except ImportError:
        logger.error("Payment router module not available — cannot create invoices")
        return None
    except Exception as e:
        logger.error(f"Payment router failed for {lead['business_name']}: {e}")
        return None

    reference = result.get("reference", "")
    if not reference:
        logger.error(f"No payment reference produced for {lead['business_name']} — invoice not created")
        await emit_event("invoice_failed", {
            "client_id": lead["id"],
            "business_name": lead["business_name"],
            "reason": result.get("error", "no reference returned"),
        })
        return None

    payment_url = result.get("url", "")
    provider = result.get("provider", "")
    payment_link_id = result.get("payment_link_id", "") or result.get("transfer_id", "")

    # Payment artifact exists — persist the deal with full provider details
    payment_instructions = result.get("payment_instructions", "")
    deal = await fetch_one(
        """INSERT INTO deals (client_id, product, amount, currency, status,
                              wise_reference, payment_url, payment_provider, payment_link_id,
                              payment_instructions)
           VALUES (%s, 'website', %s, 'USD', 'pending', %s, %s, %s, %s, %s) RETURNING id""",
        (lead["id"], amount, reference, payment_url, provider, payment_link_id, payment_instructions),
    )

    if not deal:
        return None

    if payment_url:
        logger.info(f"Invoice created for {lead['business_name']}: {payment_url}")
    else:
        logger.info(f"Invoice created for {lead['business_name']}: ref={reference}")

    # Actually deliver the invoice to the client
    delivered, delivery_error = await _deliver_invoice(lead, payment_url, reference, payment_instructions)
    return {"deal_id": deal["id"], "delivered": delivered, "delivery_error": delivery_error}


async def _deliver_invoice(
    lead: dict, payment_url: str, reference: str, payment_instructions: str = "",
) -> tuple[bool, str]:
    """Deliver an invoice to the client via the compliance email pipeline.

    Uses ``titan.compliance.send_to_instantly()`` which goes through the
    real outbound email path: compliance checks, audit logging to
    ``outbound_email_log``, and delivery via the Instantly campaign API.
    This ensures the *customer* receives the invoice, not just the operator.

    Returns (delivered: bool, error: str).
    """
    if not lead.get("email"):
        return False, "no client email address"

    client_name = lead.get("contact_name", lead.get("business_name", ""))
    business_name = lead.get("business_name", "")

    # Build a human-readable invoice body.
    # Stripe provides a payment URL; Wise provides bank transfer instructions.
    if payment_url:
        body = (
            f"Hi {client_name},\n\n"
            f"Your invoice for {business_name} is ready.\n\n"
            f"Pay here: {payment_url}\n\n"
            f"Reference: {reference}\n\n"
            f"Thank you for your business!"
        )
    elif payment_instructions:
        body = (
            f"Hi {client_name},\n\n"
            f"Your invoice for {business_name} is ready.\n\n"
            f"{payment_instructions}\n\n"
            f"Thank you for your business!"
        )
    else:
        # No URL and no instructions — cannot deliver a usable invoice
        return False, "no payment URL or instructions available"

    subject = f"Invoice for {business_name}"

    try:
        campaign_id = await _get_or_create_invoice_campaign()
        if not campaign_id:
            return False, "no Instantly invoice campaign available"

        from titan.compliance import send_to_instantly
        # Use a per-deal message_type so the compliance dedupe key
        # (client_id, campaign_id, compliance_checks->>'message_type')
        # is unique per invoice, not per client. Without this, a second
        # invoice to the same client in the shared perseus-invoices
        # campaign would be silently suppressed as a "duplicate".
        invoice_message_type = f"invoice:{reference}"
        success = await send_to_instantly(
            campaign_id=campaign_id,
            client_id=lead["id"],
            email=lead["email"],
            subject=subject,
            body=body,
            message_type=invoice_message_type,
        )
        if success:
            return True, ""
        return False, "compliance email pipeline returned failure"
    except ImportError:
        return False, "compliance module not available"
    except Exception as e:
        return False, str(e)[:300]


async def _get_or_create_invoice_campaign() -> str:
    """Reuse the dedicated invoice campaign, or create it once and persist the ID.

    Follows the same pattern as ``_get_or_create_proposals_campaign`` in
    ``titan/pipeline/close_deal.py``: check ``system_config`` first, create
    via Instantly API if missing, activate, and persist for future cycles.
    """
    campaign_id = await get_config("instantly_invoice_campaign_id", "")
    if campaign_id:
        return campaign_id

    try:
        from tools.instantly_client import InstantlyClient
        campaign_name = "perseus-invoices"
        client = InstantlyClient()
        try:
            campaigns = await client.list_campaigns()
            if isinstance(campaigns, dict):
                campaigns = campaigns.get("data", [])
            if not isinstance(campaigns, list):
                campaigns = []

            existing = next(
                (c for c in campaigns if str(c.get("name", "")).strip() == campaign_name and c.get("id")),
                None,
            )
            if existing:
                campaign_id = existing["id"]
            else:
                campaign = await client.create_campaign(campaign_name)
                campaign_id = campaign.get("id", "")

            if not campaign_id:
                return ""

            await client.activate_campaign(campaign_id)
            await set_config("instantly_invoice_campaign_id", campaign_id)
            logger.info("Invoice campaign ready: %s", campaign_id)
        finally:
            await client.close()
        return campaign_id
    except ImportError:
        logger.error("Instantly client not available — cannot create invoice campaign")
        return ""
    except Exception as e:
        logger.error("Failed to get/create invoice campaign: %s", e)
        return ""


async def _check_payments():
    """Check for received payments using a persisted cursor.

    Reads ``last_payment_check`` from system_config as a Unix timestamp
    and passes it to the payment router so the polling window starts
    from the last successful check, not a fixed lookback.

    The cursor is captured *before* the provider fetch so that payments
    created during the fetch are not lost: they will have timestamps
    >= ``poll_start`` and will be picked up by the next cycle.
    """
    import time

    try:
        from tools.payment_router import PaymentRouter
        router = PaymentRouter()
        since = int(await get_config("last_payment_check", 0))
        poll_start = int(time.time())
        result = await router.check_new_payments(since_timestamp=since)
        payments = result.get("payments", []) if isinstance(result, dict) else result
        exhausted = result.get("exhausted", True) if isinstance(result, dict) else True
    except (ImportError, Exception):
        return

    for payment in payments:
        reference = payment.get("reference", "")
        deal = None

        # Primary match: stable reference key (idempotency_key stored at invoice creation)
        if reference:
            deal = await fetch_one(
                "SELECT id, client_id FROM deals WHERE wise_reference = %s AND status = 'pending'",
                (reference,),
            )

        if not deal:
            # No reference match — log for manual reconciliation instead of
            # guessing by amount.  Amount-only matching can credit the wrong
            # client when multiple pending deals share the same price point.
            logger.warning(
                "Unmatched payment (no reference match): provider=%s amount=%s ref=%s — requires manual reconciliation",
                payment.get("provider", "?"),
                payment.get("amount", "?"),
                reference,
            )
            await emit_event("payment_unmatched", {
                "amount": payment.get("amount", 0),
                "reference": reference,
                "provider": payment.get("provider", ""),
                "metadata": payment.get("metadata", {}),
            })

        if deal:
            await execute(
                "UPDATE deals SET status = 'paid', paid_at = NOW() WHERE id = %s",
                (deal["id"],),
            )
            await transition_lead(deal["client_id"], "paid")
            await emit_event("payment_received", {
                "client_id": deal["client_id"],
                "amount": payment.get("amount", 0),
                "reference": reference,
            })
            logger.info(f"Payment received for client {deal['client_id']}!")

            # Record the learning
            # Sale already closed, but this confirms payment

    # Only advance the cursor if every provider's result set was fully
    # consumed. If any provider hit its page cap, unseen payments may
    # still exist behind the current cursor — advancing would lose them.
    if exhausted:
        await set_config("last_payment_check", poll_start)
    else:
        logger.warning(
            "Payment polling hit provider page cap — cursor NOT advanced (staying at %s)",
            since,
        )
