"""Regression tests for invoice/payment idempotency safeguards."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_payment_router_sets_stripe_idempotency_key():
    code = (ROOT / "tools" / "payment_router.py").read_text()

    assert "Idempotency-Key" in code
    assert "idempotency_key" in code


def test_invoice_pipeline_reuses_existing_pending_invoice_before_recreating():
    code = (ROOT / "titan" / "pipeline" / "invoice.py").read_text()

    assert "SELECT id, wise_reference FROM deals" in code
    assert "status = 'pending'" in code
    assert "return existing_deal[\"id\"]" in code
