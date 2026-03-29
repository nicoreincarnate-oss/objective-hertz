"""Tests that Wise invoice creation is disabled.

Verifies the payment_router.py code contracts:
- _create_wise_invoice raises RuntimeError
- create_invoice never falls through to Wise
- get_status reports 'degraded' for Wise-only
- Wise payment CHECKING is preserved
"""

from pathlib import Path


def test_wise_create_method_raises_runtime_error():
    """_create_wise_invoice must raise RuntimeError to prevent outbound transfers."""
    code = Path("tools/payment_router.py").read_text()
    # Find the method body
    in_method = False
    found_raise = False
    for line in code.splitlines():
        if "def _create_wise_invoice" in line:
            in_method = True
            continue
        if in_method:
            if line.strip().startswith("raise RuntimeError"):
                found_raise = True
                break
            if line.strip().startswith("def ") and not line.strip().startswith("def _create_wise"):
                break
    assert found_raise, "_create_wise_invoice must raise RuntimeError before any API calls"


def test_create_invoice_does_not_call_wise():
    """create_invoice must not call _create_wise_invoice."""
    code = Path("tools/payment_router.py").read_text()
    # Find the create_invoice method and check it doesn't call _create_wise_invoice
    in_method = False
    found_wise_call = False
    for line in code.splitlines():
        if "async def create_invoice(" in line:
            in_method = True
            continue
        if in_method:
            if line.strip().startswith("async def ") or line.strip().startswith("def "):
                break
            if "_create_wise_invoice" in line and "await" in line:
                found_wise_call = True
    assert not found_wise_call, "create_invoice must not call _create_wise_invoice"


def test_wise_status_is_degraded():
    """get_status must return 'degraded' when only Wise is available."""
    code = Path("tools/payment_router.py").read_text()
    # The Wise branch in get_status should use "degraded"
    in_get_status = False
    for line in code.splitlines():
        if "def get_status" in line:
            in_get_status = True
            continue
        if in_get_status:
            if line.strip().startswith("def ") or line.strip().startswith("async def "):
                break
            if "wise" in line.lower() and "degraded" in line:
                return  # Found it — test passes
    raise AssertionError("get_status Wise branch must return 'degraded', not 'live'")


def test_wise_check_payments_preserved():
    """_check_wise_payments must still exist (Wise checking is still needed)."""
    code = Path("tools/payment_router.py").read_text()
    assert "async def _check_wise_payments" in code, "Wise payment checking method was removed"
    assert "check_new_payments" in code, "check_new_payments method was removed"
