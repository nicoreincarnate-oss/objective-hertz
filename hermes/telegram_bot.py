"""
Hermes Telegram Bot — Nico's interface to Perseus.
Commands, alerts, morning briefings.
"""

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from shared.config import config
from shared.db import fetch_all, fetch_one, fetch_val, get_config
from titan.review_mode import get_pending_reviews, approve_review, reject_review

logger = logging.getLogger("perseus.hermes.telegram")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show system status."""
    pipeline = await _get_pipeline_summary()
    await update.message.reply_text(
        f"*PERSEUS Status*\n\n{pipeline}",
        parse_mode="Markdown",
    )


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active leads."""
    leads = await fetch_all(
        """SELECT status, COUNT(*) as count FROM clients
           GROUP BY status ORDER BY count DESC"""
    )
    text = "*Lead Pipeline:*\n"
    for lead in leads:
        text += f"  {lead['status']}: {lead['count']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show revenue status."""
    total = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'paid'"
    ) or 0
    pending = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM deals WHERE status = 'pending'"
    ) or 0
    await update.message.reply_text(
        f"*Revenue*\n  Collected: ${total:.2f}\n  Pending: ${pending:.2f}",
        parse_mode="Markdown",
    )


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show items pending review."""
    items = await get_pending_reviews()
    if not items:
        await update.message.reply_text("No items pending review.")
        return

    text = f"*{len(items)} items pending review:*\n\n"
    for item in items[:10]:
        text += f"  [{item['id']}] {item['item_type']} — {item.get('business_name', 'unknown')}\n"
    text += "\nUse /approve <id> or /reject <id>"
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a review queue item."""
    if not context.args:
        await update.message.reply_text("Usage: /approve <review_id>")
        return
    try:
        review_id = int(context.args[0])
        notes = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        result = await approve_review(review_id, notes)
        if result:
            await update.message.reply_text(f"Approved #{review_id}")
        else:
            await update.message.reply_text(f"Review #{review_id} not found")
    except ValueError:
        await update.message.reply_text("Invalid ID")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject a review queue item."""
    if not context.args:
        await update.message.reply_text("Usage: /reject <review_id> [reason]")
        return
    try:
        review_id = int(context.args[0])
        notes = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        result = await reject_review(review_id, notes)
        if result:
            await update.message.reply_text(f"Rejected #{review_id}")
        else:
            await update.message.reply_text(f"Review #{review_id} not found")
    except ValueError:
        await update.message.reply_text("Invalid ID")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause Titan and ClawdBot (manual — Perseus won't auto-unpause)."""
    from shared.db import set_config
    await set_config("titan_paused", True)
    await set_config("clawdbot_paused", True)
    await set_config("titan_manual_pause", True)
    await update.message.reply_text("Titan + ClawdBot PAUSED (manual). Use /resume to restart.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume Titan and ClawdBot."""
    from shared.db import set_config
    await set_config("titan_paused", False)
    await set_config("clawdbot_paused", False)
    await set_config("titan_manual_pause", False)
    await update.message.reply_text("Titan + ClawdBot RESUMED.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help."""
    await update.message.reply_text(
        "*Perseus Commands:*\n"
        "/status — System overview\n"
        "/leads — Pipeline breakdown\n"
        "/revenue — Revenue stats\n"
        "/review — Pending approvals\n"
        "/approve <id> — Approve item\n"
        "/reject <id> — Reject item\n"
        "/pause — Pause Titan\n"
        "/resume — Resume Titan\n"
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


def create_bot() -> Application:
    """Create the Telegram bot application."""
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

    return app
