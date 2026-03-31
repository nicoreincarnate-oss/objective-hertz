"""Tests for shared/governance.py — Governance / Approval System."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    """Enable the GOVERNANCE_ENABLED flag for all tests by default."""
    monkeypatch.setenv("GOVERNANCE_ENABLED", "true")


@pytest.fixture
def mock_db():
    """Mock shared.db module functions."""
    with patch("shared.governance.db") as db:
        db.execute = AsyncMock()
        db.fetch_one = AsyncMock(return_value=None)
        db.fetch_all = AsyncMock(return_value=[])
        db.emit_event = AsyncMock(return_value=1)
        yield db


# ── Tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_approval_creates_pending(mock_db):
    """request_approval creates a row with status pending."""
    from shared.governance import request_approval

    result = await request_approval(
        "budget_override", {"amount": 100}, "titan",
    )
    assert result is not None
    # Should have called execute with INSERT
    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args[0]
    assert "INSERT INTO approvals" in call_args[0]
    assert "'pending'" in call_args[0]


@pytest.mark.asyncio
async def test_request_approval_emits_event(mock_db):
    """approval_requested event is emitted."""
    from shared.governance import request_approval

    await request_approval("config_change", {"key": "test"}, "operator")
    mock_db.emit_event.assert_called_once()
    event_type = mock_db.emit_event.call_args[0][0]
    assert event_type == "approval_requested"


@pytest.mark.asyncio
async def test_resolve_approval_approve(mock_db):
    """resolve_approval with 'approved' sets status."""
    from shared.governance import resolve_approval

    mock_db.fetch_one = AsyncMock(return_value={"approval_id": "abc"})

    result = await resolve_approval("abc", "approved", "operator")
    assert result is True
    call_args = mock_db.fetch_one.call_args[0]
    assert "approved" in call_args[1]  # params tuple


@pytest.mark.asyncio
async def test_resolve_approval_reject(mock_db):
    """resolve_approval with 'rejected' sets status."""
    from shared.governance import resolve_approval

    mock_db.fetch_one = AsyncMock(return_value={"approval_id": "abc"})

    result = await resolve_approval("abc", "rejected", "operator")
    assert result is True
    call_args = mock_db.fetch_one.call_args[0]
    assert "rejected" in call_args[1]


@pytest.mark.asyncio
async def test_resolve_already_resolved_returns_false(mock_db):
    """Resolving a non-pending approval returns False."""
    from shared.governance import resolve_approval

    mock_db.fetch_one = AsyncMock(return_value=None)

    result = await resolve_approval("abc", "approved", "operator")
    assert result is False


@pytest.mark.asyncio
async def test_check_approval_required_protected_key(mock_db):
    """review_mode requires approval."""
    from shared.governance import check_approval_required

    assert await check_approval_required("config_change", config_key="review_mode") is True
    assert await check_approval_required("config_change", config_key="some_random_key") is False
    assert await check_approval_required("autonomy_transition") is True
    assert await check_approval_required("budget_override") is True


@pytest.mark.asyncio
async def test_auto_expire_stale(mock_db):
    """Approvals past expires_at get status 'expired'."""
    from shared.governance import auto_expire_stale

    mock_db.fetch_all = AsyncMock(return_value=[
        {"approval_id": "a1"},
        {"approval_id": "a2"},
    ])

    count = await auto_expire_stale()
    assert count == 2
    call_args = mock_db.fetch_all.call_args[0]
    assert "expired" in call_args[0]
    assert "expires_at < NOW()" in call_args[0]


@pytest.mark.asyncio
async def test_feature_flag_off_auto_approves(monkeypatch, mock_db):
    """get_approved returns True when flag is off (auto-approve)."""
    monkeypatch.setenv("GOVERNANCE_ENABLED", "false")
    from shared.governance import get_approved, request_approval, auto_expire_stale

    assert await get_approved("any-id") is True
    assert await request_approval("budget_override", {}, "test") is None
    assert await auto_expire_stale() == 0
    mock_db.execute.assert_not_called()
    mock_db.fetch_one.assert_not_called()
