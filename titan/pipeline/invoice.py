"""
Stage 10: Invoice & Payment
Send invoices and track payments. Works with Mexican bank account.
"""

import logging

from shared.db import fetch_all, fetch_one, execute, emit_event
from shared.config import config
from shared.pipeline_alerts import emit_pipeline_error
from titan.state_machine import transition_lead

logger = logging.getLogger("perseus.titan.invoice")


async def process_invoices():
    """Send invoices for deployed sites and check for payments."""
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
            invoice_id = await _create_and_send_invoice(lead)
            if invoice_id:
                await transition_lead(lead["id"], "invoiced")
                await emit_event("invoice_sent", {
                    "client_id": lead["id"],
                    "business_name": lead["business_name"],
                })
                logger.info(f"Invoice sent to {lead['business_name']}")
        except Exception as e:
            logger.error(f"Invoice failed for lead {lead['id']}: {e}")
            await emit_pipeline_error("invoice", e, lead_id=lead["id"])


async def _create_and_send_invoice(lead: dict) -> int | None:
    """Create an invoice and send it. Returns deal ID only if a payment link/reference was produced."""
    amount = config.pricing.website_5page  # Default 5-page price
    description = f"Professional 5-page website for {lead['business_name']}"

    existing_deal = await fetch_one(
        """SELECT id, wise_reference FROM deals
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
        return existing_deal["id"]

    # Send via payment platform first — don't create a deal record until we have a deliverable invoice
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

    # Payment link/reference exists — now create the deal record
    deal = await fetch_one(
        """INSERT INTO deals (client_id, product, amount, currency, status, wise_reference)
           VALUES (%s, 'website', %s, 'USD', 'pending', %s) RETURNING id""",
        (lead["id"], amount, reference),
    )

    payment_url = result.get("url", "")
    if payment_url:
        logger.info(f"Invoice created for {lead['business_name']}: {payment_url}")
    else:
        logger.info(f"Invoice created for {lead['business_name']}: ref={reference}")

    return deal["id"] if deal else None


async def _check_payments():
    """Check for received payments."""
    try:
        from tools.payment_router import PaymentRouter
        router = PaymentRouter()
        payments = await router.check_new_payments()
    except (ImportError, Exception):
        return

    for payment in payments:
        reference = payment.get("reference", "")
        deal = await fetch_one(
            "SELECT id, client_id FROM deals WHERE wise_reference = %s AND status = 'pending'",
            (reference,),
        )
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
            from titan.pipeline.close_deal import mark_sale_closed
            # Sale already closed, but this confirms payment
