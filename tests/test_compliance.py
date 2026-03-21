"""Tests for the centralized email compliance gate."""

import asyncio
import importlib
import sys
import types
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch


def run(coro):
    """Run async code without depending on pytest-asyncio."""
    return asyncio.run(coro)


def load_compliance_module():
    """Import titan.compliance with DB/Jsonb dependencies stubbed for unit tests."""
    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock()
    fake_db.execute = AsyncMock()
    fake_db.fetch_one = AsyncMock()
    fake_db.get_config = AsyncMock()

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg_types = types.ModuleType("psycopg.types")
    fake_json = types.ModuleType("psycopg.types.json")

    class FakeJsonb:
        def __init__(self, value):
            self.value = value

    fake_json.Jsonb = FakeJsonb
    fake_psycopg.types = fake_psycopg_types
    fake_psycopg_types.json = fake_json

    sys.modules.pop("titan.compliance", None)
    sys.modules["shared.db"] = fake_db
    sys.modules["psycopg"] = fake_psycopg
    sys.modules["psycopg.types"] = fake_psycopg_types
    sys.modules["psycopg.types.json"] = fake_json
    module = importlib.import_module("titan.compliance")

    return module


def test_send_refuses_when_unsubscribe_secret_missing():
    """The signing secret must come from env, not system_config."""
    compliance = load_compliance_module()

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        if key == "unsubscribe_secret":
            raise AssertionError("unsubscribe_secret should not be read from system_config")
        return values.get(key, default)

    with patch.dict("os.environ", {}, clear=False):
        with patch.object(compliance, "fetch_one", AsyncMock(return_value={"status": "email_drafted"})):
            with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
                result = run(
                    compliance.send_to_instantly(
                        campaign_id="cmp_123",
                        client_id=42,
                        email="hello@example.com",
                        subject="Hi",
                        body="Body",
                    )
                )

    assert result is False


def test_assert_compliance_ready_raises_and_alerts():
    """Titan startup should fail fast and emit an alert when compliance config is missing."""
    compliance = load_compliance_module()

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    with patch.dict("os.environ", {}, clear=False):
        with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
            with patch.object(compliance, "emit_event", AsyncMock()) as emit_event:
                try:
                    run(compliance.assert_compliance_ready())
                except RuntimeError as exc:
                    message = str(exc)
                else:
                    raise AssertionError("assert_compliance_ready() should raise")

    assert "UNSUBSCRIBE_SECRET" in message
    emit_event.assert_awaited_once()


def test_assert_compliance_ready_rejects_placeholder_address():
    """Placeholder mailing addresses must fail readiness even if JSON-style quoted."""
    compliance = load_compliance_module()

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": '"[SET YOUR PHYSICAL ADDRESS]"',
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    with patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "super-secret-key"}):
        with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
            try:
                run(compliance.assert_compliance_ready())
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError("assert_compliance_ready() should raise for placeholder address")

    assert "company_address" in message


def test_generate_unsub_link_uses_128_bit_signature():
    """Public unsubscribe links should carry at least 128 bits of signature."""
    compliance = load_compliance_module()

    link = compliance.generate_unsub_link(
        client_id=42,
        secret="super-secret-key",
        base_url="https://perseus.mx",
    )
    parsed = urlparse(link)
    sig = parse_qs(parsed.query)["sig"][0]

    assert parsed.path == "/unsub"
    assert len(sig) == 32


def test_send_injects_footer_and_logs_outbound_email():
    """A successful send must log the pending attempt before send, then finalize it."""
    compliance = load_compliance_module()

    sent = {}
    db_calls = []

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "SELECT status FROM clients" in query:
            return {"status": "email_drafted"}
        if "INSERT INTO outbound_email_log" in query:
            db_calls.append(("insert", query, params))
            return {"id": 99}
        return None

    async def fake_execute(query: str, params: tuple = ()):
        db_calls.append(("execute", query, params))

    class FakeInstantlyClient:
        async def add_lead(self, **kwargs):
            sent.update(kwargs)
            return {"id": "lead_123"}

        async def close(self):
            return None

    with patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "super-secret-key"}):
        with patch.object(compliance, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
            with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch.object(compliance, "execute", AsyncMock(side_effect=fake_execute)):
                    with patch("tools.instantly_client.InstantlyClient", FakeInstantlyClient):
                        result = run(
                            compliance.send_to_instantly(
                                campaign_id="cmp_123",
                                client_id=42,
                                email="hello@example.com",
                                subject="Hi",
                                body="Personalized body",
                                seq_id=7,
                                first_name="Nico",
                            )
                        )

    assert result is True
    assert sent["campaign_id"] == "cmp_123"
    assert sent["email"] == "hello@example.com"
    assert sent["custom_subject"] == "Hi"
    assert sent["personalization"] == "Personalized body"
    assert "Calle Example 123" in sent["compliance_footer"]
    assert "https://perseus.mx/unsub?id=42&sig=" in sent["compliance_footer"]
    insert_call = db_calls[0]
    assert insert_call[0] == "insert"
    assert "INSERT INTO outbound_email_log" in insert_call[1]
    assert "send_status, compliance_checks" in insert_call[1]
    assert "'pending'" in insert_call[1]
    assert insert_call[2][0] == 42
    assert insert_call[2][1] == 7
    assert insert_call[2][2] == "hello@example.com"
    assert insert_call[2][6].value["message_type"] == "email"
    finalize_call = db_calls[1]
    assert "UPDATE outbound_email_log" in finalize_call[1]
    assert "send_status = 'sent'" in finalize_call[1]


def test_send_marks_failed_attempt_when_provider_send_fails():
    compliance = load_compliance_module()

    db_calls = []

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "SELECT status FROM clients" in query:
            return {"status": "email_drafted"}
        if "INSERT INTO outbound_email_log" in query:
            db_calls.append(("insert", query, params))
            return {"id": 101}
        return None

    async def fake_execute(query: str, params: tuple = ()):
        db_calls.append(("execute", query, params))

    class FakeInstantlyClient:
        async def add_lead(self, **kwargs):
            raise RuntimeError("boom")

        async def close(self):
            return None

    with patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "super-secret-key"}):
        with patch.object(compliance, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
            with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch.object(compliance, "execute", AsyncMock(side_effect=fake_execute)):
                    with patch("tools.instantly_client.InstantlyClient", FakeInstantlyClient):
                        result = run(
                            compliance.send_to_instantly(
                                campaign_id="cmp_123",
                                client_id=42,
                                email="hello@example.com",
                                subject="Hi",
                                body="Personalized body",
                            )
                        )

    assert result is False
    assert db_calls[0][0] == "insert"
    assert "INSERT INTO outbound_email_log" in db_calls[0][1]
    assert "send_status = 'failed'" in db_calls[1][1]


def test_send_skips_resend_when_sequence_already_sent():
    compliance = load_compliance_module()

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "SELECT status FROM clients" in query:
            return {"status": "email_drafted"}
        if "WHERE email_sequence_id = %s" in query:
            return {"id": 55, "send_status": "sent"}
        return None

    class FakeInstantlyClient:
        async def add_lead(self, **kwargs):
            raise AssertionError("add_lead should not run when sequence was already sent")

        async def close(self):
            return None

    with patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "super-secret-key"}):
        with patch.object(compliance, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
            with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch("tools.instantly_client.InstantlyClient", FakeInstantlyClient):
                    result = run(
                        compliance.send_to_instantly(
                            campaign_id="cmp_123",
                            client_id=42,
                            email="hello@example.com",
                            subject="Hi",
                            body="Body",
                            seq_id=7,
                        )
                    )

    assert result is True


def test_send_recovers_pending_sequence_without_resend():
    compliance = load_compliance_module()

    async def fake_get_config(key: str, default=None):
        values = {
            "company_address": "Calle Example 123, El Sargento, BCS",
            "unsubscribe_base_url": "https://perseus.mx",
        }
        return values.get(key, default)

    async def fake_fetch_one(query: str, params: tuple = ()):
        if "SELECT status FROM clients" in query:
            return {"status": "email_drafted"}
        if "WHERE email_sequence_id = %s" in query:
            return {"id": 56, "send_status": "pending"}
        return None

    class FakeInstantlyClient:
        async def add_lead(self, **kwargs):
            raise AssertionError("add_lead should not run when pending row is recovered")

        async def close(self):
            return None

    with patch.dict("os.environ", {"UNSUBSCRIBE_SECRET": "super-secret-key"}):
        with patch.object(compliance, "fetch_one", AsyncMock(side_effect=fake_fetch_one)):
            with patch.object(compliance, "get_config", AsyncMock(side_effect=fake_get_config)):
                with patch.object(compliance, "execute", AsyncMock()) as execute:
                    with patch.object(compliance, "emit_event", AsyncMock()) as emit_event:
                        with patch("tools.instantly_client.InstantlyClient", FakeInstantlyClient):
                            result = run(
                                compliance.send_to_instantly(
                                    campaign_id="cmp_123",
                                    client_id=42,
                                    email="hello@example.com",
                                    subject="Hi",
                                    body="Body",
                                    seq_id=7,
                                )
                            )

    assert result is True
    execute.assert_awaited_once()
    emit_event.assert_awaited_once()


def test_proposal_sends_are_tagged_in_compliance_checks():
    code = open("/Users/majovega/Desktop/objective-hertz/titan/pipeline/close_deal.py").read()
    review_code = open("/Users/majovega/Desktop/objective-hertz/titan/review_mode.py").read()

    assert 'message_type="proposal"' in code
    assert 'message_type="proposal" if item.get("item_type") == "proposal" else "email"' in review_code
