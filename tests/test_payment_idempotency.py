"""Behavioral tests for invoice/payment idempotency and reconciliation safety.

Tests that:
1. PaymentRouter sets Stripe Idempotency-Key header
2. PaymentRouter propagates perseus_key to PaymentIntent metadata
3. Invoice pipeline reuses existing pending deals instead of creating duplicates
4. Idempotency keys are deterministic for the same inputs
5. _check_payments refuses amount-only matching (no wrong-client crediting)
6. Payment polling paginates and uses cursor checkpoints (no lost payments)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestIdempotencyKeyGeneration:
    """The idempotency key must be deterministic and stable."""

    def test_same_inputs_produce_same_key(self):
        from tools.payment_router import _invoice_idempotency_key

        key1 = _invoice_idempotency_key("test@example.com", 500.00, "Website", "USD")
        key2 = _invoice_idempotency_key("test@example.com", 500.00, "Website", "USD")
        assert key1 == key2

    def test_different_inputs_produce_different_keys(self):
        from tools.payment_router import _invoice_idempotency_key

        key1 = _invoice_idempotency_key("a@example.com", 500.00, "Website", "USD")
        key2 = _invoice_idempotency_key("b@example.com", 500.00, "Website", "USD")
        assert key1 != key2

    def test_email_case_normalized(self):
        from tools.payment_router import _invoice_idempotency_key

        key1 = _invoice_idempotency_key("Test@Example.COM", 500.00, "Website", "USD")
        key2 = _invoice_idempotency_key("test@example.com", 500.00, "Website", "USD")
        assert key1 == key2

    def test_key_is_hex_string(self):
        from tools.payment_router import _invoice_idempotency_key

        key = _invoice_idempotency_key("test@example.com", 100.0, "desc", "USD")
        assert len(key) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in key)


class TestStripeIdempotency:
    """Stripe payment creation must include idempotency headers and PI metadata."""

    @pytest.mark.asyncio
    async def test_stripe_sets_idempotency_key_header(self):
        """The Stripe POST must include an Idempotency-Key header."""

        captured_request = {}

        async def mock_post(url, **kwargs):
            captured_request.update(kwargs)
            resp = MagicMock()
            resp.json.return_value = {"id": "plink_123", "url": "https://pay.stripe.com/test"}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("tools.payment_router.env_is_configured", return_value=True):
                with patch("tools.payment_router.config") as cfg:
                    cfg.payment.stripe_api_key = "sk_test_xxx"
                    from tools.payment_router import PaymentRouter
                    router = PaymentRouter()
                    router._stripe_available = True
                    result = await router._create_stripe_invoice(
                        "test@example.com", "Test Co", 500.0, "Website", "USD", "idem_key_123"
                    )

        assert captured_request["headers"]["Idempotency-Key"] == "idem_key_123"
        assert captured_request["data"]["payment_intent_data[metadata][perseus_key]"] == "idem_key_123"
        assert result["reference"] == "idem_key_123"

    @pytest.mark.asyncio
    async def test_stripe_propagates_metadata_to_payment_intent(self):
        """payment_intent_data[metadata] must be set so PI reconciliation works."""

        captured_data = {}

        async def mock_post(url, **kwargs):
            captured_data.update(kwargs.get("data", {}))
            resp = MagicMock()
            resp.json.return_value = {"id": "plink_456", "url": "https://pay.stripe.com/test"}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("tools.payment_router.config") as cfg:
                cfg.payment.stripe_api_key = "sk_test_xxx"
                from tools.payment_router import PaymentRouter
                router = PaymentRouter()
                router._stripe_available = True
                await router._create_stripe_invoice(
                    "client@co.com", "Client Co", 300.0, "Hosting", "USD", "key_abc"
                )

        # These fields bridge PL → Checkout → PI metadata gap
        assert captured_data.get("payment_intent_data[metadata][perseus_key]") == "key_abc"
        assert captured_data.get("payment_intent_data[metadata][client_email]") == "client@co.com"


class TestInvoicePipelineDedup:
    """Invoice pipeline must reuse existing pending deals."""

    @pytest.mark.asyncio
    async def test_existing_pending_deal_is_reused(self):
        """When a pending deal with a reference exists, no new invoice is created."""
        existing = {"id": 42, "wise_reference": "ref_existing", "payment_url": "https://pay.stripe.com/test", "payment_provider": "stripe", "payment_instructions": ""}

        async def mock_send_to_instantly(**kwargs):
            return True

        with patch("titan.pipeline.invoice.fetch_one", AsyncMock(return_value=existing)), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0
            from titan.pipeline.invoice import _create_and_send_invoice

            lead = {"id": 1, "business_name": "TestCo", "email": "test@co.com", "contact_name": "Test", "language": "en"}
            result = await _create_and_send_invoice(lead)

        assert result["deal_id"] == 42  # Returns the existing deal ID

    @pytest.mark.asyncio
    async def test_new_deal_created_when_no_pending_exists(self):
        """When no pending deal exists, the pipeline creates a new invoice."""
        call_count = {"fetch": 0}

        async def mock_fetch(query, params=None):
            call_count["fetch"] += 1
            if call_count["fetch"] == 1:
                return None  # No existing deal
            return {"id": 99}  # Newly created deal

        mock_router = AsyncMock()
        mock_router.create_invoice = AsyncMock(return_value={
            "reference": "ref_new", "url": "https://pay.example.com", "provider": "stripe",
            "payment_link_id": "plink_1",
        })

        async def mock_send_to_instantly(**kwargs):
            return True

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router), \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0
            from titan.pipeline.invoice import _create_and_send_invoice

            lead = {"id": 2, "business_name": "NewCo", "email": "new@co.com", "contact_name": "New", "language": "en"}
            result = await _create_and_send_invoice(lead)

        # Should have queried for existing, then created new
        assert call_count["fetch"] == 2
        assert result["deal_id"] == 99


class TestReconciliationSafety:
    """_check_payments must not match by amount alone."""

    @pytest.mark.asyncio
    async def test_unmatched_payment_emits_event_not_wrong_deal(self):
        """A payment with no reference match must NOT credit a deal by amount."""
        events_emitted = []
        deals_updated = []

        async def mock_fetch_one(query, params=None):
            # No deal matches the reference
            return None

        async def mock_execute(query, params=None):
            deals_updated.append((query, params))

        async def mock_emit_event(event_type, payload):
            events_emitted.append((event_type, payload))

        async def mock_transition(*a, **kw):
            pass

        mock_router = MagicMock()
        mock_router.check_new_payments = AsyncMock(return_value=[
            {"reference": "unknown_ref", "amount": 500.0, "provider": "stripe", "metadata": {}},
        ])

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one):
            with patch("titan.pipeline.invoice.execute", mock_execute):
                with patch("titan.pipeline.invoice.emit_event", mock_emit_event):
                    with patch("titan.pipeline.invoice.transition_lead", mock_transition):
                        with patch("titan.pipeline.invoice.get_config", AsyncMock(return_value=0)):
                            with patch("titan.pipeline.invoice.set_config", AsyncMock()):
                                with patch("tools.payment_router.PaymentRouter", return_value=mock_router):
                                    from titan.pipeline.invoice import _check_payments
                                    await _check_payments()

        # No deal should have been updated (no UPDATE deals SET status = 'paid')
        paid_updates = [d for d in deals_updated if "paid" in str(d[0])]
        assert len(paid_updates) == 0, f"Should not have updated any deal, but got: {paid_updates}"

        # Should have emitted payment_unmatched event
        unmatched = [e for e in events_emitted if e[0] == "payment_unmatched"]
        assert len(unmatched) == 1
        assert unmatched[0][1]["amount"] == 500.0
        assert unmatched[0][1]["reference"] == "unknown_ref"

    @pytest.mark.asyncio
    async def test_matched_payment_credits_correct_deal(self):
        """A payment with a matching reference must credit the right deal."""
        deals_updated = []
        events_emitted = []

        async def mock_fetch_one(query, params=None):
            if "wise_reference" in query and params and params[0] == "ref_abc":
                return {"id": 42, "client_id": 7}
            return None

        async def mock_execute(query, params=None):
            deals_updated.append((query, params))

        async def mock_emit_event(event_type, payload):
            events_emitted.append((event_type, payload))

        mock_router = MagicMock()
        mock_router.check_new_payments = AsyncMock(return_value=[
            {"reference": "ref_abc", "amount": 500.0, "provider": "stripe", "metadata": {}},
        ])

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one):
            with patch("titan.pipeline.invoice.execute", mock_execute):
                with patch("titan.pipeline.invoice.emit_event", mock_emit_event):
                    with patch("titan.pipeline.invoice.transition_lead", AsyncMock()):
                        with patch("titan.pipeline.invoice.get_config", AsyncMock(return_value=0)):
                            with patch("titan.pipeline.invoice.set_config", AsyncMock()):
                                with patch("tools.payment_router.PaymentRouter", return_value=mock_router):
                                    from titan.pipeline.invoice import _check_payments
                                    await _check_payments()

        # Deal 42 should have been marked paid
        paid_updates = [d for d in deals_updated if "paid" in str(d[0])]
        assert len(paid_updates) == 1
        assert paid_updates[0][1] == (42,)

        # payment_received event should reference client 7
        received = [e for e in events_emitted if e[0] == "payment_received"]
        assert len(received) == 1
        assert received[0][1]["client_id"] == 7


class TestPollingCursorAndPagination:
    """Payment polling must use cursors and paginate, not drop payments."""

    @pytest.mark.asyncio
    async def test_stripe_paginates_with_starting_after(self):
        """Stripe polling must follow has_more pages, not stop at 20."""
        page_count = {"n": 0}

        async def mock_get(url, **kwargs):
            page_count["n"] += 1
            params = kwargs.get("params", {})
            resp = MagicMock()
            if page_count["n"] == 1:
                resp.json.return_value = {
                    "data": [
                        {"id": f"pi_{i}", "status": "succeeded", "amount": 50000,
                         "currency": "usd", "metadata": {"perseus_key": f"key_{i}"}}
                        for i in range(100)
                    ],
                    "has_more": True,
                }
            else:
                resp.json.return_value = {
                    "data": [
                        {"id": "pi_final", "status": "succeeded", "amount": 30000,
                         "currency": "usd", "metadata": {"perseus_key": "key_final"}}
                    ],
                    "has_more": False,
                }
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("tools.payment_router.config") as cfg:
                cfg.payment.stripe_api_key = "sk_test_xxx"
                from tools.payment_router import PaymentRouter
                router = PaymentRouter()
                router._stripe_available = True
                result = await router._check_stripe_payments(since_timestamp=1000000)

        assert page_count["n"] == 2, f"Should have paginated to 2 pages, got {page_count['n']}"
        assert len(result["payments"]) == 101  # 100 from page 1 + 1 from page 2
        assert result["exhausted"] is True

    @pytest.mark.asyncio
    async def test_check_payments_persists_cursor_before_fetch(self):
        """_check_payments must capture the cursor before the fetch, not after.

        This prevents the boundary race: payments created during the fetch
        would be missed if the cursor were set to wall-clock-after-processing.
        """
        import time

        config_store = {"last_payment_check": 0}
        fetch_timestamp = None

        async def mock_get_config(key, default=None):
            return config_store.get(key, default)

        async def mock_set_config(key, value):
            config_store[key] = value

        async def mock_check_new_payments(since_timestamp=0):
            nonlocal fetch_timestamp
            fetch_timestamp = time.time()
            # Simulate slow provider fetch
            import asyncio
            await asyncio.sleep(0.05)
            return []

        mock_router = MagicMock()
        mock_router.check_new_payments = mock_check_new_payments

        with patch("titan.pipeline.invoice.get_config", mock_get_config):
            with patch("titan.pipeline.invoice.set_config", mock_set_config):
                with patch("titan.pipeline.invoice.fetch_one", AsyncMock(return_value=None)):
                    with patch("tools.payment_router.PaymentRouter", return_value=mock_router):
                        from titan.pipeline.invoice import _check_payments
                        await _check_payments()

        # Cursor must have been captured BEFORE the fetch, not after
        # So the persisted cursor should be <= the fetch start time
        assert config_store["last_payment_check"] <= fetch_timestamp
        # And it should be approximately now (within a few seconds)
        assert config_store["last_payment_check"] > time.time() - 10

    def test_default_lookback_is_7_days(self):
        """Default lookback window must be 7 days, not 24 hours."""
        import time

        from tools.payment_router import _default_lookback_timestamp

        ts = _default_lookback_timestamp()
        expected = int(time.time()) - 7 * 86400
        assert abs(ts - expected) < 5  # within 5 seconds


# ═══════════════════════════════════════════════════════════════
# Cycle 7 — Finding 1: invoice delivery truthfulness
# ═══════════════════════════════════════════════════════════════


class TestInvoiceDelivery:
    """Invoice pipeline must actually deliver invoices and report honestly."""

    @pytest.mark.asyncio
    async def test_invoice_not_transitioned_when_delivery_fails(self):
        """If delivery fails, status must NOT transition to 'invoiced'."""
        events_emitted = []
        transitions = []

        async def mock_fetch_one(query, params=None):
            if "SELECT" in query:
                return None  # No existing deal
            return {"id": 99}  # INSERT RETURNING

        async def mock_emit(event_type, payload):
            events_emitted.append((event_type, payload))

        async def mock_transition(client_id, status):
            transitions.append((client_id, status))

        # Compliance email pipeline fails to deliver
        async def mock_send_to_instantly(**kwargs):
            return False

        mock_router = AsyncMock()
        mock_router.create_invoice = AsyncMock(return_value={
            "reference": "ref_123", "url": "https://pay.stripe.com/test",
            "provider": "stripe", "payment_link_id": "plink_1",
        })

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one), \
             patch("titan.pipeline.invoice.fetch_all", AsyncMock(return_value=[
                 {"id": 1, "business_name": "TestCo", "contact_name": "Test", "email": "t@co.com", "language": "en"}
             ])), \
             patch("titan.pipeline.invoice.emit_event", mock_emit), \
             patch("titan.pipeline.invoice.transition_lead", mock_transition), \
             patch("titan.pipeline.invoice.execute", AsyncMock()), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("titan.pipeline.invoice.emit_pipeline_error", AsyncMock()), \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router), \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0
            from titan.pipeline.invoice import _send_invoices
            await _send_invoices()

        # Must NOT have transitioned to invoiced
        assert not any(s == "invoiced" for _, s in transitions), f"Should not transition to invoiced: {transitions}"
        # Must have emitted invoice_created_not_delivered, not invoice_sent
        event_types = [e[0] for e in events_emitted]
        assert "invoice_sent" not in event_types
        assert "invoice_created_not_delivered" in event_types

    @pytest.mark.asyncio
    async def test_invoice_transitioned_when_delivery_succeeds(self):
        """If delivery succeeds, status should transition to 'invoiced'."""
        transitions = []
        events_emitted = []

        async def mock_fetch_one(query, params=None):
            if "SELECT" in query:
                return None
            return {"id": 99}

        async def mock_transition(client_id, status):
            transitions.append((client_id, status))

        async def mock_emit(event_type, payload):
            events_emitted.append((event_type, payload))

        # Compliance email pipeline succeeds
        async def mock_send_to_instantly(**kwargs):
            return True

        mock_router = AsyncMock()
        mock_router.create_invoice = AsyncMock(return_value={
            "reference": "ref_123", "url": "https://pay.stripe.com/test",
            "provider": "stripe", "payment_link_id": "plink_1",
        })

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one), \
             patch("titan.pipeline.invoice.fetch_all", AsyncMock(return_value=[
                 {"id": 1, "business_name": "TestCo", "contact_name": "Test", "email": "t@co.com", "language": "en"}
             ])), \
             patch("titan.pipeline.invoice.emit_event", mock_emit), \
             patch("titan.pipeline.invoice.transition_lead", mock_transition), \
             patch("titan.pipeline.invoice.execute", AsyncMock()), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("titan.pipeline.invoice.emit_pipeline_error", AsyncMock()), \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router), \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0
            from titan.pipeline.invoice import _send_invoices
            await _send_invoices()

        assert any(s == "invoiced" for _, s in transitions)
        event_types = [e[0] for e in events_emitted]
        assert "invoice_sent" in event_types

    @pytest.mark.asyncio
    async def test_wise_invoice_delivers_payment_instructions(self):
        """Wise invoices must deliver bank transfer instructions via compliance pipeline."""
        send_kwargs_list = []

        async def mock_fetch_one(query, params=None):
            if "SELECT" in query:
                return None
            return {"id": 99}

        async def mock_send_to_instantly(**kwargs):
            send_kwargs_list.append(kwargs)
            return True

        mock_router = AsyncMock()
        mock_router.create_invoice = AsyncMock(return_value={
            "reference": "ref_wise", "url": "", "provider": "wise",
            "transfer_id": "tr_1", "payment_link_id": "tr_1",
            "payment_instructions": "Bank transfer reference: ref_wise\nWise transfer ID: tr_1\nAmount: 500.0 USD",
        })

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one), \
             patch("titan.pipeline.invoice.fetch_all", AsyncMock(return_value=[
                 {"id": 1, "business_name": "WiseCo", "contact_name": "Wise", "email": "w@co.com", "language": "en"}
             ])), \
             patch("titan.pipeline.invoice.emit_event", AsyncMock()), \
             patch("titan.pipeline.invoice.transition_lead", AsyncMock()), \
             patch("titan.pipeline.invoice.execute", AsyncMock()), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("titan.pipeline.invoice.emit_pipeline_error", AsyncMock()), \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router), \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0
            from titan.pipeline.invoice import _send_invoices
            await _send_invoices()

        assert len(send_kwargs_list) >= 1
        body = send_kwargs_list[0].get("body", "")
        assert "Bank transfer reference" in body
        assert "ref_wise" in body

    @pytest.mark.asyncio
    async def test_wise_invoice_without_instructions_not_delivered(self):
        """Wise invoice with no URL and no instructions must not claim delivery."""
        with patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_1")):
            from titan.pipeline.invoice import _deliver_invoice
            lead = {"email": "t@co.com", "contact_name": "Test", "business_name": "Co", "id": 1}
            delivered, error = await _deliver_invoice(lead, "", "ref_123", "")
        assert delivered is False
        assert "no payment URL or instructions" in error

    @pytest.mark.asyncio
    async def test_delivery_fails_when_no_campaign_available(self):
        """If no Instantly invoice campaign can be created, delivery must fail."""
        with patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="")):
            from titan.pipeline.invoice import _deliver_invoice
            lead = {"email": "t@co.com", "contact_name": "Test", "business_name": "Co", "id": 1}
            delivered, error = await _deliver_invoice(lead, "https://pay.stripe.com/test", "ref_123")
        assert delivered is False
        assert "campaign" in error.lower()

    @pytest.mark.asyncio
    async def test_second_invoice_to_same_client_uses_unique_message_type(self):
        """Two invoices to the same client must use different message_types for dedupe."""
        send_calls = []

        async def mock_send_to_instantly(**kwargs):
            send_calls.append(kwargs)
            return True

        async def mock_fetch_one(query, params=None):
            if "SELECT" in query:
                return None
            return {"id": len(send_calls) + 100}

        mock_router = AsyncMock()

        with patch("titan.pipeline.invoice.fetch_one", mock_fetch_one), \
             patch("titan.pipeline.invoice.execute", AsyncMock()), \
             patch("titan.pipeline.invoice.config") as cfg, \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router), \
             patch("titan.compliance.send_to_instantly", mock_send_to_instantly), \
             patch("titan.pipeline.invoice._get_or_create_invoice_campaign", AsyncMock(return_value="camp_inv_1")):
            cfg.pricing.website_5page = 500.0

            from titan.pipeline.invoice import _create_and_send_invoice

            # First invoice — Stripe with URL
            mock_router.create_invoice = AsyncMock(return_value={
                "reference": "ref_first", "url": "https://pay.stripe.com/1",
                "provider": "stripe", "payment_link_id": "plink_1",
            })
            lead = {"id": 1, "business_name": "Co", "contact_name": "Test", "email": "t@co.com", "language": "en"}
            await _create_and_send_invoice(lead)

            # Second invoice — different reference
            mock_router.create_invoice = AsyncMock(return_value={
                "reference": "ref_second", "url": "https://pay.stripe.com/2",
                "provider": "stripe", "payment_link_id": "plink_2",
            })
            await _create_and_send_invoice(lead)

        assert len(send_calls) == 2
        mt1 = send_calls[0]["message_type"]
        mt2 = send_calls[1]["message_type"]
        assert mt1 != mt2, f"message_types must differ per invoice, got: {mt1}, {mt2}"
        assert "ref_first" in mt1
        assert "ref_second" in mt2


# ═══════════════════════════════════════════════════════════════
# Cycle 7 — Finding 2: Stripe fallback duplicate prevention
# ═══════════════════════════════════════════════════════════════


class TestStripeFallbackSafety:
    """Stripe-to-Wise fallback must use idempotent retry before creating a second artifact."""

    @pytest.mark.asyncio
    async def test_stripe_idempotent_retry_succeeds_prevents_wise_fallback(self):
        """On ambiguous Stripe failure, idempotent retry returns original PL → no Wise call."""
        post_calls = []

        async def mock_post(url, **kwargs):
            post_calls.append(url)
            if len(post_calls) == 1:
                # First call: ambiguous failure
                raise ConnectionError("ambiguous timeout")
            # Second call (idempotent retry): Stripe returns the original PL
            resp = MagicMock()
            resp.json.return_value = {"id": "plink_recovered", "url": "https://pay.stripe.com/recovered"}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("tools.payment_router.config") as cfg:
            cfg.payment.stripe_api_key = "sk_test_xxx"
            from tools.payment_router import PaymentRouter
            router = PaymentRouter()
            router._stripe_available = True
            router._wise_available = True
            result = await router._create_stripe_invoice(
                "test@co.com", "Test", 500.0, "Website", "USD", "key_123"
            )

        assert len(post_calls) == 2, f"Expected 2 POST calls (original + retry), got {len(post_calls)}"
        assert result["provider"] == "stripe"
        assert result["payment_link_id"] == "plink_recovered"

    @pytest.mark.asyncio
    async def test_stripe_double_failure_refuses_wise_fallback(self):
        """If Stripe and idempotent retry both fail, must NOT create Wise artifact."""
        wise_called = {"called": False}

        async def mock_post(url, **kwargs):
            if "wise.com" in url:
                wise_called["called"] = True
            # All Stripe POSTs fail with transport error
            raise ConnectionError("stripe down")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("tools.payment_router.config") as cfg:
            cfg.payment.stripe_api_key = "sk_test_xxx"
            cfg.payment.wise_api_token = "wise_test"
            cfg.payment.wise_profile_id = "profile_1"
            from tools.payment_router import PaymentRouter
            router = PaymentRouter()
            router._stripe_available = True
            router._wise_available = True
            result = await router._create_stripe_invoice(
                "test@co.com", "Test", 500.0, "Website", "USD", "key_456"
            )

        assert not wise_called["called"], "Must NOT fall back to Wise when Stripe state is ambiguous"
        assert result["reference"] == ""
        assert "ambiguous" in result.get("error", "").lower()


# ═══════════════════════════════════════════════════════════════
# Cycle 7 — Finding 4: cursor must not advance when cap hit
# ═══════════════════════════════════════════════════════════════


class TestPollingCapExhaustion:
    """Polling must not advance cursor when page cap is hit."""

    @pytest.mark.asyncio
    async def test_cursor_not_advanced_when_stripe_cap_hit(self):
        """If Stripe hits page cap (has_more still True), cursor stays put."""

        config_store = {"last_payment_check": 1000}
        page_count = {"n": 0}

        async def mock_get_config(key, default=None):
            return config_store.get(key, default)

        async def mock_set_config(key, value):
            config_store[key] = value

        # Stripe returns has_more=True on every page (simulates cap exhaustion)
        async def mock_get(url, **kwargs):
            page_count["n"] += 1
            resp = MagicMock()
            resp.json.return_value = {
                "data": [{"id": f"pi_{page_count['n']}", "status": "succeeded",
                          "amount": 50000, "currency": "usd",
                          "metadata": {"perseus_key": f"key_{page_count['n']}"}}],
                "has_more": True,  # Always more — cap will be hit
            }
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get

        mock_router_class = MagicMock()

        with patch("titan.pipeline.invoice.get_config", mock_get_config), \
             patch("titan.pipeline.invoice.set_config", mock_set_config), \
             patch("titan.pipeline.invoice.fetch_one", AsyncMock(return_value=None)), \
             patch("titan.pipeline.invoice.emit_event", AsyncMock()), \
             patch("titan.pipeline.invoice.transition_lead", AsyncMock()), \
             patch("titan.pipeline.invoice.execute", AsyncMock()), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("tools.payment_router.env_is_configured", return_value=True), \
             patch("tools.payment_router.config") as cfg:
            cfg.payment.stripe_api_key = "sk_test_xxx"
            cfg.payment.wise_api_token = ""
            cfg.payment.wise_profile_id = ""
            from tools.payment_router import PaymentRouter
            router = PaymentRouter()
            router._stripe_available = True
            router._wise_available = False
            router._conway_available = False

            with patch("tools.payment_router.PaymentRouter", return_value=router):
                from titan.pipeline.invoice import _check_payments
                await _check_payments()

        # Cursor must NOT have advanced because the cap was hit
        assert config_store["last_payment_check"] == 1000, \
            f"Cursor should stay at 1000 but advanced to {config_store['last_payment_check']}"

    @pytest.mark.asyncio
    async def test_cursor_advances_when_fully_exhausted(self):
        """If all providers finish cleanly, cursor should advance."""

        config_store = {"last_payment_check": 1000}

        async def mock_get_config(key, default=None):
            return config_store.get(key, default)

        async def mock_set_config(key, value):
            config_store[key] = value

        mock_router = MagicMock()
        mock_router.check_new_payments = AsyncMock(return_value={
            "payments": [], "exhausted": True,
        })

        with patch("titan.pipeline.invoice.get_config", mock_get_config), \
             patch("titan.pipeline.invoice.set_config", mock_set_config), \
             patch("titan.pipeline.invoice.fetch_one", AsyncMock(return_value=None)), \
             patch("tools.payment_router.PaymentRouter", return_value=mock_router):
            from titan.pipeline.invoice import _check_payments
            await _check_payments()

        # Cursor should have advanced (to approximately now)
        assert config_store["last_payment_check"] > 1000
