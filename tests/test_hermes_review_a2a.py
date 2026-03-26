"""Behavioral tests for the Hermes → Titan review A2A path.

These tests verify that:
1. cmd_approve / cmd_reject call the correct comms API with the right args
2. Success and failure responses from Titan are handled correctly
3. Titan's A2A handlers return the expected dict shape
4. The full path is routable (capabilities + static routing)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Hermes side: cmd_approve / cmd_reject behaviour
# ---------------------------------------------------------------------------


def _make_update_and_context(chat_id: str, args: list[str]):
    """Build minimal Telegram Update + Context mocks."""
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = int(chat_id)
    update.effective_message = MagicMock()
    # reply_text is async in python-telegram-bot
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message

    context = MagicMock()
    context.args = args
    return update, context


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Stub out shared.config so Hermes module loads without .env."""
    cfg = MagicMock()
    cfg.telegram.chat_id = "12345"
    cfg.telegram.bot_token = "fake-token"
    monkeypatch.setenv("TELEGRAM_ADMIN_SECRET", "s3cret")
    monkeypatch.setattr("shared.config.config", cfg)
    # Force-import and patch the module-level binding so the auth check sees
    # our mock config, not whatever was cached from a prior test module.
    import hermes.telegram_bot  # noqa: ensure loaded
    monkeypatch.setattr("hermes.telegram_bot.config", cfg)


class TestCmdApprove:
    """cmd_approve must call call_agent_capability and handle its response."""

    @pytest.mark.asyncio
    async def test_approve_success_calls_titan(self, monkeypatch):
        from hermes.telegram_bot import cmd_approve

        update, ctx = _make_update_and_context("12345", ["42", "s3cret", "looks", "good"])

        mock_cap = AsyncMock(return_value={"success": True, "review_id": 42})
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_approve(update, ctx)

        # Verify the correct A2A call was made
        mock_cap.assert_awaited_once_with(
            "titan", "review_approve",
            {"review_id": 42, "notes": "looks good"},
        )
        # Verify the user got a success reply
        reply_text = update.effective_message.reply_text
        reply_text.assert_awaited_once()
        assert "Approved #42" in reply_text.await_args[0][0]

    @pytest.mark.asyncio
    async def test_approve_failure_shows_error(self, monkeypatch):
        from hermes.telegram_bot import cmd_approve

        update, ctx = _make_update_and_context("12345", ["99", "s3cret"])

        mock_cap = AsyncMock(return_value={"success": False, "error": "not found"})
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_approve(update, ctx)

        reply_text = update.effective_message.reply_text
        reply_text.assert_awaited_once()
        assert "not found" in reply_text.await_args[0][0]

    @pytest.mark.asyncio
    async def test_approve_titan_unreachable(self, monkeypatch):
        """When Titan is down, call_agent_capability returns None."""
        from hermes.telegram_bot import cmd_approve

        update, ctx = _make_update_and_context("12345", ["42", "s3cret"])

        mock_cap = AsyncMock(return_value=None)
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_approve(update, ctx)

        reply_text = update.effective_message.reply_text
        reply_text.assert_awaited_once()
        # Should show failure, not crash
        assert "not found or action failed" in reply_text.await_args[0][0]


class TestCmdReject:
    """cmd_reject must call call_agent_capability and handle its response."""

    @pytest.mark.asyncio
    async def test_reject_success(self, monkeypatch):
        from hermes.telegram_bot import cmd_reject

        update, ctx = _make_update_and_context("12345", ["7", "s3cret", "spam"])

        mock_cap = AsyncMock(return_value={"success": True, "review_id": 7})
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_reject(update, ctx)

        mock_cap.assert_awaited_once_with(
            "titan", "review_reject",
            {"review_id": 7, "notes": "spam"},
        )
        reply_text = update.effective_message.reply_text
        assert "Rejected #7" in reply_text.await_args[0][0]

    @pytest.mark.asyncio
    async def test_reject_failure(self, monkeypatch):
        from hermes.telegram_bot import cmd_reject

        update, ctx = _make_update_and_context("12345", ["7", "s3cret"])

        mock_cap = AsyncMock(return_value={"success": False, "error": "already rejected"})
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_reject(update, ctx)

        reply_text = update.effective_message.reply_text
        assert "already rejected" in reply_text.await_args[0][0]

    @pytest.mark.asyncio
    async def test_reject_titan_unreachable(self, monkeypatch):
        """When Titan is down, call_agent_capability returns None."""
        from hermes.telegram_bot import cmd_reject

        update, ctx = _make_update_and_context("12345", ["7", "s3cret"])

        mock_cap = AsyncMock(return_value=None)
        monkeypatch.setattr("hermes.telegram_bot.call_agent_capability", mock_cap)

        await cmd_reject(update, ctx)

        reply_text = update.effective_message.reply_text
        reply_text.assert_awaited_once()
        assert "not found or action failed" in reply_text.await_args[0][0]


# ---------------------------------------------------------------------------
# Titan side: A2A handler contract
# ---------------------------------------------------------------------------


class TestTitanReviewHandlers:
    """Titan's _review_approve / _review_reject must return the correct dict shape."""

    @pytest.mark.asyncio
    async def test_review_approve_success(self, monkeypatch):
        from titan.a2a_server import _review_approve

        mock_approve = AsyncMock(return_value=True)
        monkeypatch.setattr("titan.a2a_server.approve_review", mock_approve, raising=False)
        # Patch the lazy import inside the handler
        with patch("titan.review_mode.approve_review", mock_approve):
            result = await _review_approve(review_id=42, notes="lgtm")

        assert result == {"success": True, "review_id": 42}

    @pytest.mark.asyncio
    async def test_review_approve_not_found(self, monkeypatch):
        from titan.a2a_server import _review_approve

        mock_approve = AsyncMock(return_value=False)
        with patch("titan.review_mode.approve_review", mock_approve):
            result = await _review_approve(review_id=999, notes="")

        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_review_approve_missing_id(self):
        from titan.a2a_server import _review_approve

        result = await _review_approve(review_id=0)
        assert result["success"] is False
        assert "review_id is required" in result["error"]

    @pytest.mark.asyncio
    async def test_review_reject_success(self, monkeypatch):
        from titan.a2a_server import _review_reject

        mock_reject = AsyncMock(return_value=True)
        with patch("titan.review_mode.reject_review", mock_reject):
            result = await _review_reject(review_id=7, notes="spam")

        assert result == {"success": True, "review_id": 7}

    @pytest.mark.asyncio
    async def test_review_reject_missing_id(self):
        from titan.a2a_server import _review_reject

        result = await _review_reject(review_id=0)
        assert result["success"] is False
        assert "review_id is required" in result["error"]


# ---------------------------------------------------------------------------
# Routing: capabilities and static routing are wired
# ---------------------------------------------------------------------------


class TestReviewRouting:
    """Verify the A2A path is actually routable."""

    def test_titan_card_advertises_review_capabilities(self):
        from titan.a2a_server import TITAN_CARD

        assert "review_approve" in TITAN_CARD.capabilities
        assert "review_reject" in TITAN_CARD.capabilities

    def test_static_routing_maps_to_titan(self):
        from shared.task_routing import TASK_ROUTING

        assert TASK_ROUTING["review_approve"] == "titan"
        assert TASK_ROUTING["review_reject"] == "titan"

    def test_capability_handlers_include_review(self):
        from titan.a2a_server import CAPABILITY_HANDLERS

        assert "review_approve" in CAPABILITY_HANDLERS
        assert "review_reject" in CAPABILITY_HANDLERS
        # Verify they're actually async callables, not None
        assert callable(CAPABILITY_HANDLERS["review_approve"])
        assert callable(CAPABILITY_HANDLERS["review_reject"])
