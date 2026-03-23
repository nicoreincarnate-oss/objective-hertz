"""
Hermes Telegram Bot — Nico's interface to Perseus.
Commands, alerts, morning briefings.
"""

import hmac
import json
import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from shared.config import config
from shared.db import fetch_all, fetch_val, get_config
from titan.review_mode import approve_review, get_pending_reviews, reject_review

logger = logging.getLogger("perseus.hermes.telegram")


async def _reply(update: Update, text: str, *, parse_mode: str | None = None) -> None:
    """Reply via the effective message when present."""
    message = update.effective_message
    if not message:
        return
    await message.reply_text(text, parse_mode=parse_mode)


def _args(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return list(context.args or [])


async def _require_chat_access(update: Update) -> bool:
    """Allow commands only from Nico's configured Telegram chat."""
    configured_chat = str(config.telegram.chat_id).strip()
    actual_chat = str(update.effective_chat.id if update.effective_chat else "").strip()

    if not configured_chat:
        logger.warning("TELEGRAM_CHAT_ID is not configured — denying Telegram command")
        if update.message:
            await _reply(update, "Telegram control is not configured.")
        return False

    if not hmac.compare_digest(actual_chat, configured_chat):
        logger.warning("Unauthorized Telegram chat attempted command access: %s", actual_chat or "unknown")
        if update.message:
            await _reply(update, "Unauthorized.")
        return False

    return True


async def _require_destructive_auth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    usage: str,
    secret_arg_index: int,
) -> bool:
    """Require both authorized chat access and a second secret for destructive actions."""
    if not await _require_chat_access(update):
        return False

    configured_secret = os.getenv("TELEGRAM_ADMIN_SECRET", "").strip()
    if not configured_secret:
        logger.warning("TELEGRAM_ADMIN_SECRET is not configured — denying destructive Telegram command")
        if update.message:
            await _reply(update, "Telegram admin secret is not configured.")
        return False

    args = _args(context)
    if len(args) <= secret_arg_index:
        if update.message:
            await _reply(update, usage)
        return False

    provided_secret = args[secret_arg_index].strip()
    if not hmac.compare_digest(provided_secret, configured_secret):
        logger.warning(
            "Telegram destructive command rejected due to invalid admin secret from chat %s",
            update.effective_chat.id if update.effective_chat else "unknown",
        )
        if update.message:
            await _reply(update, "Unauthorized.")
        return False

    return True


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system status."""
    if not await _require_chat_access(update):
        return
    pipeline = await _get_pipeline_summary()
    await _reply(update, 
        f"*PERSEUS Status*\n\n{pipeline}",
        parse_mode="Markdown",
    )


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active leads."""
    if not await _require_chat_access(update):
        return
    leads = await fetch_all(
        """SELECT status, COUNT(*) as count FROM clients
           GROUP BY status ORDER BY count DESC"""
    )
    text = "*Lead Pipeline:*\n"
    for lead in leads:
        text += f"  {lead['status']}: {lead['count']}\n"
    await _reply(update, text, parse_mode="Markdown")


async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show revenue status."""
    if not await _require_chat_access(update):
        return
    total = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
    ) or 0
    pending = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'pending'"
    ) or 0
    await _reply(update, 
        f"*Revenue*\n  Collected: ${total:.2f}\n  Pending: ${pending:.2f}",
        parse_mode="Markdown",
    )


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show items pending review."""
    if not await _require_chat_access(update):
        return
    items = await get_pending_reviews()
    if not items:
        await _reply(update, "No items pending review.")
        return

    text = f"*{len(items)} items pending review:*\n\n"
    for item in items[:10]:
        text += f"  [{item['id']}] {item['item_type']} — {item.get('business_name', 'unknown')}\n"
    text += "\nUse /approve <id> or /reject <id>"
    await _reply(update, text, parse_mode="Markdown")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a review queue item."""
    if not await _require_destructive_auth(
        update,
        context,
        usage="Usage: /approve <review_id> <admin_secret> [notes]",
        secret_arg_index=1,
    ):
        return
    try:
        args = _args(context)
        review_id = int(args[0])
        notes = " ".join(args[2:]) if len(args) > 2 else ""
        result = await approve_review(review_id, notes)
        if result:
            await _reply(update, f"Approved #{review_id}")
        else:
            await _reply(update, f"Review #{review_id} not found")
    except ValueError:
        await _reply(update, "Invalid ID")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a review queue item."""
    if not await _require_destructive_auth(
        update,
        context,
        usage="Usage: /reject <review_id> <admin_secret> [reason]",
        secret_arg_index=1,
    ):
        return
    try:
        args = _args(context)
        review_id = int(args[0])
        notes = " ".join(args[2:]) if len(args) > 2 else ""
        result = await reject_review(review_id, notes)
        if result:
            await _reply(update, f"Rejected #{review_id}")
        else:
            await _reply(update, f"Review #{review_id} not found")
    except ValueError:
        await _reply(update, "Invalid ID")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause Titan and ClawdBot (manual — Perseus won't auto-unpause)."""
    if not await _require_destructive_auth(
        update,
        context,
        usage="Usage: /pause <admin_secret>",
        secret_arg_index=0,
    ):
        return
    from shared.db import set_config
    await set_config("titan_paused", True)
    await set_config("clawdbot_paused", True)
    await set_config("titan_manual_pause", True)
    await _reply(update, "Titan + ClawdBot PAUSED (manual). Use /resume to restart.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume Titan and ClawdBot."""
    if not await _require_destructive_auth(
        update,
        context,
        usage="Usage: /resume <admin_secret>",
        secret_arg_index=0,
    ):
        return
    from shared.db import set_config
    await set_config("titan_paused", False)
    await set_config("clawdbot_paused", False)
    await set_config("titan_manual_pause", False)
    await _reply(update, "Titan + ClawdBot RESUMED.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help."""
    if not await _require_chat_access(update):
        return
    await _reply(update, 
        "*Perseus Commands:*\n"
        "/status — System overview\n"
        "/leads — Pipeline breakdown\n"
        "/revenue — Revenue stats\n"
        "/review — Pending approvals\n"
        "/approve <id> <secret> — Approve item\n"
        "/reject <id> <secret> — Reject item\n"
        "/pause <secret> — Pause Titan\n"
        "/resume <secret> — Resume Titan\n"
        "/help — This message",
        parse_mode="Markdown",
    )


async def _get_pipeline_summary() -> str:
    """Generate a pipeline summary."""
    total_leads = await fetch_val("SELECT COUNT(*) FROM clients") or 0
    emails_today = await fetch_val(
        "SELECT COALESCE(SUM(emails_sent), 0) FROM outreach_metrics WHERE date = CURRENT_DATE"
    ) or 0
    interested = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status = 'interested'"
    ) or 0
    closed = await fetch_val(
        "SELECT COUNT(*) FROM clients WHERE status IN ('closed', 'building', 'deployed', 'invoiced', 'paid')"
    ) or 0
    review_pending = await fetch_val(
        "SELECT COUNT(*) FROM review_queue WHERE status = 'pending_review'"
    ) or 0
    review_mode = await get_config("review_mode", True)

    return (
        f"Leads: {total_leads}\n"
        f"Emails today: {emails_today}\n"
        f"Interested: {interested}\n"
        f"Closed/Delivered: {closed}\n"
        f"Pending review: {review_pending}\n"
        f"Review mode: {'ON' if review_mode else 'OFF (autonomous)'}"
    )


async def cmd_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages — forward to the boss (OpenJarvis) as commands."""
    if not await _require_chat_access(update):
        return

    text = update.effective_message.text if update.effective_message else ""
    if not text or text.startswith("/"):
        return

    logger.info(f"Operator message (forwarding to boss): {text[:100]}")

    # Store the message as an event
    from shared.db import execute
    await execute(
        "INSERT INTO events (event_type, payload) VALUES (%s, %s)",
        ("operator_message", json.dumps({"text": text[:500], "source": "telegram"})),
    )

    # Forward to OpenJarvis as a boss command
    try:
        from shared.comms import delegate_task
        await delegate_task("hermes", "orchestrator", "operator_command",
            {"text": text}, priority=1)
        await _reply(update, f"Got it. Forwarded to OpenJarvis.")
    except Exception as e:
        logger.error(f"Failed to forward to boss: {e}")
        await _reply(update, f"Message received but couldn't reach the boss: {e}")


def create_bot() -> Application:
    """Create the Telegram bot application."""
    from telegram.ext import MessageHandler, filters

    app = Application.builder().token(config.telegram.bot_token).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CommandHandler("revenue", cmd_revenue))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    # Plain text messages → forward to boss
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_message))

    return app
