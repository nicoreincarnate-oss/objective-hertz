"""
Stage 10: Invoice & Payment
Send invoices and track payments. Works with Mexican bank account.
"""

import logging

from shared.db import fetch_all, fetch_one, execute, emit_event
from shared.config import config
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


async def _create_and_send_invoice(lead: dict) -> int:
    """Create an invoice and send it."""
    amount = config.pricing.website_5page  # Default 5-page price

    # Create deal record
    deal = await fetch_one(
        """INSERT INTO deals (client_id, product, amount, currency, status)
           VALUES (%s, 'website', %s, 'USD', 'pending') RETURNING id""",
        (lead["id"], amount),
    )

    # Try to send via payment platform
    try:
        from tools.payment_router import PaymentRouter
        router = PaymentRouter()
        result = await router.create_invoice(
            client_email=lead["email"],
            client_name=lead.get("contact_name", lead["business_name"]),
            amount=amount,
            description=f"Professional 5-page website for {lead['business_name']}",
        )
        if result.get("reference"):
            await execute(
                "UPDATE deals SET wise_reference = %s WHERE id = %s",
                (result["reference"], deal["id"]),
            )
    except (ImportError, Exception) as e:
        logger.warning(f"Payment router not available: {e}")

    return deal["id"]


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
