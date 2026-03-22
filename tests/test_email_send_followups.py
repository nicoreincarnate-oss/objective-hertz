"""Regression tests for follow-up email sending."""

import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch


def run(coro):
    """Run async code without pytest-asyncio."""
    return asyncio.run(coro)


def load_email_send_module():
    """Import titan.pipeline.email_send with lightweight stubs."""
    fake_db = types.ModuleType("shared.db")
    fake_db.fetch_all = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.get_config = AsyncMock()
    fake_db.set_config = AsyncMock()
    fake_db.emit_event = AsyncMock()
    fake_db.transaction = AsyncMock()

    fake_state_machine = types.ModuleType("titan.state_machine")
    fake_state_machine.transition_lead = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    sys.modules.pop("titan.pipeline.email_send", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["titan.state_machine"] = fake_state_machine
    sys.modules["titan.training"] = fake_training
    return importlib.import_module("titan.pipeline.email_send")


def test_send_emails_fetches_pending_follow_ups_too():
    """Later sequence steps should be eligible for sending."""
    email_send = load_email_send_module()

    async def fake_fetch_all(query: str, params: tuple = ()):
        assert "es.status = 'pending'" in query
        assert "c.status IN ('email_drafted', 'followed_up')" in query
        assert "es.step = 1" not in query
        return []

    with patch.object(email_send, "fetch_all", AsyncMock(side_effect=fake_fetch_all)):
        with patch.object(email_send, "get_config", AsyncMock(return_value=False)):
            run(email_send.send_emails())


def test_add_lead_to_campaign_does_not_rewind_follow_up_status():
    """Sending a later follow-up should not transition the lead back to email_sent."""
    email_send = load_email_send_module()

    lead = {
        "client_id": 42,
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
        "industry": "Plumbing",
        "city": "La Paz",
        "country": "MX",
        "seq_id": 7,
        "step": 2,
        "client_status": "followed_up",
        "subject": "Checking back in",
        "body": "Short follow-up",
    }

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(email_send, "fetch_one", AsyncMock(return_value=None)):
        with patch.object(email_send, "collect_training_example", AsyncMock()):
            with patch.object(email_send, "transaction", fake_transaction):
                result = run(email_send._add_lead_to_campaign("cmp_123", lead))

    assert result is True
    assert len(conn.calls) == 2


def test_add_lead_to_campaign_wraps_first_send_updates_in_one_transaction():
    """Sequence/client updates should commit atomically after a successful send."""
    email_send = load_email_send_module()

    lead = {
        "client_id": 42,
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
        "industry": "Plumbing",
        "city": "La Paz",
        "country": "MX",
        "seq_id": 7,
        "step": 1,
        "client_status": "email_queued",
        "subject": "Hello",
        "body": "Body",
    }

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(email_send, "fetch_one", AsyncMock(return_value=None)):
        with patch.object(email_send, "collect_training_example", AsyncMock()):
            with patch.object(email_send, "transaction", fake_transaction):
                result = run(email_send._add_lead_to_campaign("cmp_123", lead))

    assert result is True
    assert len(conn.calls) == 2


def test_add_lead_to_campaign_records_simulation_before_sending():
    """A safe draft should store simulation output before it is sent."""
    email_send = load_email_send_module()

    lead = {
        "client_id": 42,
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
        "industry": "Plumbing",
        "city": "La Paz",
        "country": "MX",
        "seq_id": 7,
        "step": 1,
        "client_status": "email_queued",
        "subject": "Quick idea for Acme",
        "body": "I noticed your site could convert more visitors into calls.",
    }

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    class FakeConn:
        def __init__(self):
            self.calls = []

        async def execute(self, query: str, params: tuple = ()):
            self.calls.append((query, params))

    conn = FakeConn()

    @asynccontextmanager
    async def fake_transaction():
        yield conn

    with patch.object(email_send, "execute", AsyncMock()) as mocked_execute:
        with patch.object(email_send, "fetch_one", AsyncMock(return_value=None)):
            with patch.object(email_send, "collect_training_example", AsyncMock()):
                with patch.object(email_send, "transaction", fake_transaction):
                    result = run(email_send._add_lead_to_campaign("cmp_123", lead))

    assert result is True
    mocked_execute.assert_awaited()
    first_query = mocked_execute.await_args_list[0].args[0]
    assert "simulation_status" in first_query
    assert "simulation_personas" in first_query
    fake_compliance.send_to_instantly.assert_awaited_once()


def test_add_lead_to_campaign_skips_risky_draft_and_flags_it():
    """A risky draft should be flagged locally instead of being sent."""
    email_send = load_email_send_module()

    lead = {
        "client_id": 99,
        "email": "hello@example.com",
        "business_name": "Acme",
        "contact_name": "Nico Vega",
        "industry": "Plumbing",
        "city": "La Paz",
        "country": "MX",
        "seq_id": 11,
        "step": 1,
        "client_status": "email_queued",
        "subject": "ACT NOW!!! FREE WEBSITE",
        "body": "CLICK HERE for $$$ and guaranteed results!!!",
    }

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)
    sys.modules["titan.compliance"] = fake_compliance

    with patch.object(email_send, "execute", AsyncMock()) as mocked_execute:
        with patch.object(email_send, "fetch_one", AsyncMock()):
            with patch.object(email_send, "collect_training_example", AsyncMock()):
                result = run(email_send._add_lead_to_campaign("cmp_123", lead))

    assert result is False
    mocked_execute.assert_awaited()
    first_query = mocked_execute.await_args_list[0].args[0]
    first_params = mocked_execute.await_args_list[0].args[1]
    assert "simulation_status" in first_query
    assert first_params[0] == "flagged"
    fake_compliance.send_to_instantly.assert_not_awaited()
