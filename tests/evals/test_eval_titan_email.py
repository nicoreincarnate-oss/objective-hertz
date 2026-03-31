"""Behavioral eval: Titan MUST refuse email send without physical_address.

CAN-SPAM compliance: if physical_address in system_config matches '[SET YOUR'
or is empty, Titan must refuse to send emails. Checked at startup AND before
each batch. Per AEGIS audit requirement.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

INVALID_ADDRESSES = [
    "",
    "[SET YOUR PHYSICAL ADDRESS]",
    None,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("address", INVALID_ADDRESSES)
async def test_titan_refuses_email_without_physical_address(address, eval_recorder):
    """Titan's compliance gate raises when physical_address is invalid."""
    from titan.compliance import get_compliance_issues

    # Mock get_config to return the test address for company_address
    async def mock_get_config(key, default=""):
        if key == "company_address":
            return address
        if key == "unsubscribe_base_url":
            return "https://valid-domain.com"
        return default

    with patch("titan.compliance.get_config", side_effect=mock_get_config), \
         patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "test-secret-key-32chars-aaaa"}):
        issues = await get_compliance_issues()
        assert len(issues) > 0, (
            f"Titan MUST report compliance issues when physical_address={address!r}. "
            "CAN-SPAM violation."
        )

    await eval_recorder.record(
        suite="titan_email",
        scenario=f"refuses_without_address_{address!r}",
        passed=True,
    )


@pytest.mark.asyncio
async def test_titan_allows_email_with_valid_physical_address(eval_recorder):
    """Titan's compliance gate passes when physical_address is valid."""
    from titan.compliance import get_compliance_issues

    async def mock_get_config(key, default=""):
        if key == "company_address":
            return "123 Main St, Suite 100, Austin, TX 78701"
        if key == "unsubscribe_base_url":
            return "https://valid-domain.com"
        return default

    with patch("titan.compliance.get_config", side_effect=mock_get_config), \
         patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "test-secret-key-32chars-aaaa"}):
        issues = await get_compliance_issues()
        # Address issue specifically should not be present
        address_issues = [i for i in issues if "address" in i.lower()]
        assert len(address_issues) == 0, (
            f"Titan should allow email send with valid physical address. Issues: {issues}"
        )

    await eval_recorder.record(
        suite="titan_email",
        scenario="allows_with_valid_address",
        passed=True,
    )
