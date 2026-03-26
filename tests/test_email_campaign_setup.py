"""Behavioral tests for email_send.py review queue dedup and delivery honesty — Cycle 21."""

import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from psycopg.types.json import Jsonb
except ImportError:
    Jsonb = None


class FakeConn:
    def __init__(self):
        self.queries = []
    async def execute(self, query, params=None):
        self.queries.append((query, params))


class FakeTransaction:
    def __init__(self):
        self.conn = FakeConn()
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, *a):
        pass


def _setup_fakes():
    modules_to_fake = [
        "shared.db", "shared.pipeline_alerts", "titan.state_machine",
        "titan.training", "titan.compliance", "titan.pipeline.email_send",
    ]
    saved = {k: sys.modules.get(k) for k in modules_to_fake}

    fake_db = types.ModuleType("shared.db")
    fake_db.emit_event = AsyncMock(return_value=1)
    fake_db.execute = AsyncMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    fake_db.fetch_one = AsyncMock(return_value=None)
    fake_db.get_config = AsyncMock(return_value=None)
    fake_db.set_config = AsyncMock()
    fake_db.insert_task = AsyncMock(return_value=1)

    fake_txn = FakeTransaction()
    fake_db.transaction = MagicMock(return_value=fake_txn)

    fake_alerts = types.ModuleType("shared.pipeline_alerts")
    fake_alerts.emit_pipeline_error = AsyncMock()

    fake_state = types.ModuleType("titan.state_machine")
    fake_state.transition_lead = AsyncMock()

    fake_training = types.ModuleType("titan.training")
    fake_training.collect_training_example = AsyncMock()

    fake_compliance = types.ModuleType("titan.compliance")
    fake_compliance.send_to_instantly = AsyncMock(return_value=True)

    sys.modules["shared.db"] = fake_db
    sys.modules["shared.pipeline_alerts"] = fake_alerts
    sys.modules["titan.state_machine"] = fake_state
    sys.modules["titan.training"] = fake_training
    sys.modules["titan.compliance"] = fake_compliance
    sys.modules.pop("titan.pipeline.email_send", None)

    es_mod = importlib.import_module("titan.pipeline.email_send")
    return saved, es_mod, fake_db, fake_state, fake_txn


def _restore(saved):
    for mod_name, orig in saved.items():
        if orig is not None:
            sys.modules[mod_name] = orig
        else:
            sys.modules.pop(mod_name, None)


def test_queue_for_review_skips_already_queued_drafts():
    """_queue_for_review must not insert a duplicate review_queue row for the same seq_id."""
    saved, es_mod, fake_db, fake_state, _ = _setup_fakes()
    try:
        fake_state.transition_lead = AsyncMock()
        execute_calls = []

        async def capturing_execute(query, params=None):
            execute_calls.append((query, params))

        async def mock_fetch_all(query, params=None):
            return [
                {"client_id": 1, "email": "t@co.com", "business_name": "Co",
                 "client_status": "email_drafted", "seq_id": 10, "step": 1,
                 "subject": "Hello", "body": "Test body"},
            ]

        async def mock_fetch_one(query, params=None):
            if "review_queue" in query:
                return {"id": 99}
            return None

        with patch.object(es_mod, "fetch_all", mock_fetch_all), \
             patch.object(es_mod, "fetch_one", mock_fetch_one), \
             patch.object(es_mod, "execute", capturing_execute):
            asyncio.run(es_mod._queue_for_review(50))

        review_inserts = [q for q, p in execute_calls if "review_queue" in q]
        assert len(review_inserts) == 0, f"Should not insert duplicate review item: {review_inserts}"
    finally:
        _restore(saved)


def test_add_lead_sets_email_queued_not_email_sent():
    """_add_lead_to_campaign must set client status to 'email_queued', not 'email_sent'."""
    saved, es_mod, fake_db, _, fake_txn = _setup_fakes()
    try:
        fake_txn.conn = FakeConn()

        async def mock_send(**kwargs):
            return True

        fake_db.fetch_one = AsyncMock(return_value={"research_summary": "", "industry": "", "language": "en"})

        lead = {"client_id": 1, "email": "t@co.com", "business_name": "Co",
                "seq_id": 10, "step": 1, "subject": "Hello", "body": "Test",
                "contact_name": "Test", "industry": "", "city": "", "country": ""}

        with patch("titan.compliance.send_to_instantly", mock_send):
            result = asyncio.run(es_mod._add_lead_to_campaign("camp_1", lead))

        assert result is True
        seq_updates = [q for q, p in fake_txn.conn.queries if "email_sequences" in q and "status" in q]
        assert len(seq_updates) >= 1
        assert "'queued'" in seq_updates[0], f"Expected 'queued' for sequence status, got: {seq_updates[0]}"
        assert "'sent'" not in seq_updates[0], f"Sequence must not use 'sent': {seq_updates[0]}"
        client_updates = [q for q, p in fake_txn.conn.queries if "clients" in q and "status" in q]
        assert len(client_updates) >= 1
        assert "email_queued" in client_updates[0], f"Expected email_queued, got: {client_updates[0]}"
        assert "email_sent" not in client_updates[0], f"Must not use email_sent: {client_updates[0]}"
    finally:
        _restore(saved)
