"""Regression tests for review-mode follow-up sends."""

import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch


def run(coro):
    return asyncio.run(coro)


def load_review_mode_module():
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.transaction = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    sys.modules.pop("titan.review_mode", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["titan.state_machine"] = fake_state_machine
    return importlib.import_module("titan.review_mode")


def test_send_approved_follow_up_updates_sequence_without_rewinding_status():
    review_mode = load_review_mode_module()

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    item = {
        "client_id": 42,
        "content": {
            "seq_id": 7,
            "step": 2,
            "subject": "Quick follow-up",
            "body": "Still interested?",
        },
    }

    lead = {
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
    }

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(review_mode, "fetch_one", AsyncMock(return_value=lead)):
        with patch.object(review_mode, "get_config", AsyncMock(return_value="cmp_123")):
            with patch.object(review_mode, "transaction", fake_transaction):
                with patch.object(review_mode, "transition_lead", AsyncMock()) as transition:
                    run(review_mode._send_approved_email(item))

    transition.assert_not_awaited()
    assert (
        "UPDATE email_sequences SET status = 'sent', sent_at = NOW() WHERE id = %s",
        (7,),
    ) in conn.calls


def test_send_approved_first_email_updates_in_one_transaction():
    review_mode = load_review_mode_module()

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    item = {
        "client_id": 42,
        "content": {
            "seq_id": 7,
            "step": 1,
            "subject": "Hello",
            "body": "Body",
        },
    }

    lead = {
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
        "status": "email_queued",
    }

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(review_mode, "fetch_one", AsyncMock(return_value=lead)):
        with patch.object(review_mode, "get_config", AsyncMock(return_value="cmp_123")):
            with patch.object(review_mode, "transaction", fake_transaction):
                with patch.object(review_mode, "transition_lead", AsyncMock()) as transition:
                    run(review_mode._send_approved_email(item))

    transition.assert_awaited_once_with(42, "email_sent", conn=conn)
    assert all("SET status = 'email_sent'" not in query for query, _ in conn.calls)
    assert len(conn.calls) == 2


def test_send_approved_first_email_stops_when_transition_is_invalid():
    review_mode = load_review_mode_module()

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    item = {
        "client_id": 42,
        "content": {
            "seq_id": 7,
            "step": 1,
            "subject": "Hello",
            "body": "Body",
        },
    }

    lead = {
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
    }

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(review_mode, "fetch_one", AsyncMock(return_value=lead)):
        with patch.object(review_mode, "get_config", AsyncMock(return_value="cmp_123")):
            with patch.object(review_mode, "transaction", fake_transaction):
                with patch.object(review_mode, "transition_lead", AsyncMock(return_value=False)) as transition:
                    result = run(review_mode._send_approved_email(item))

    assert result is False
    transition.assert_awaited_once_with(42, "email_sent", conn=conn)
    assert conn.calls == []


def test_approve_review_keeps_item_pending_when_send_fails():
    review_mode = load_review_mode_module()

    item = {
        "id": 5,
        "client_id": 42,
        "item_type": "email_draft",
        "content": {
            "seq_id": 7,
            "step": 1,
            "subject": "Hello",
            "body": "Body",
        },
    }

    with patch.object(review_mode, "fetch_one", AsyncMock(return_value=item)):
        with patch.object(review_mode, "_send_approved_email", AsyncMock(return_value=False)):
            result = run(review_mode.approve_review(5, "ship it"))

    assert result is False
    review_mode.execute.assert_not_awaited()
    review_mode.emit_event.assert_not_awaited()
