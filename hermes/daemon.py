"""
Hermes Daemon — starts Telegram bot, alert dispatcher, and web dashboard.
"""

import asyncio
import os
import signal

import uvicorn

from hermes.alerts import dispatch_alerts
from hermes.telegram_bot import create_bot
from shared import db
from shared.agent_base import AgentBase
from shared.config import config
from shared.logging_config import setup_logging
from shared.observability import capture_exception, install_asyncio_exception_handler

logger = setup_logging("hermes")

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8500"))


class HermesDaemon(AgentBase):
    name = "hermes"
    description = "Intelligence & relationship layer — Telegram interface, alerts, briefings."

    def __init__(self):
        super().__init__()
        self._running = False
        self._alert_interval = 30  # Check for alerts every 30 seconds

    async def start(self):
        """Start Hermes — web dashboard + Telegram bot + alert dispatcher."""
        logger.info("Hermes starting up...")
        await db.init_pool()

        # Boot integration modules (telemetry, tool registry, channels, etc.)
        from shared.integration_boot import boot_integration
        integration_status = await boot_integration("hermes")
        logger.info("Integration boot status: %s", integration_status)

        await self.register()
        self._stopped.clear()
        self._running = True

        # Start the web dashboard in a background task (skip if standalone dashboard is running)
        dashboard_task = None
        if not os.getenv("SKIP_INTERNAL_DASHBOARD"):
            dashboard_task = asyncio.create_task(self._run_dashboard())

        try:
            _token = (config.telegram.bot_token or "").strip()
            _placeholder_tokens = {"", "CHANGE_ME", "your-bot-token-here"}
            if not _token or _token in _placeholder_tokens:
                logger.warning("TELEGRAM_BOT_TOKEN not set or placeholder — running dashboard + alerts only")
                await asyncio.gather(self._alert_loop(), self._active_forward_loop())
            else:
                # Run bot and alert dispatcher concurrently
                bot = create_bot()
                try:
                    await bot.initialize()
                except Exception as e:
                    logger.warning("Telegram bot failed to initialize (%s) — running dashboard + alerts only", e)
                    await asyncio.gather(self._alert_loop(), self._active_forward_loop())
                    return  # Do NOT fall through to bot.start() on a failed bot
                await bot.start()
                try:
                    await bot.updater.start_polling()
                    logger.info("Hermes Telegram bot is LIVE.")

                    # Run alert loop and active forward loop alongside the bot
                    forward_task = asyncio.create_task(self._active_forward_loop())
                    try:
                        while self._running:
                            self.begin_work("loop:alerts")
                            try:
                                await dispatch_alerts()
                            finally:
                                self.finish_work("loop:alerts")
                            await asyncio.sleep(self._alert_interval)
                    finally:
                        forward_task.cancel()
                finally:
                    await bot.updater.stop()
                    await bot.stop()
                    await bot.shutdown()
        finally:
            dashboard_task.cancel()
            await self.finalize_shutdown()

    async def _run_dashboard(self):
        """Run the FastAPI dashboard via uvicorn in the same process."""
        from hermes.web.app import app

        uvicorn_config = uvicorn.Config(
            app,
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        logger.info("Dashboard starting on http://%s:%d", DASHBOARD_HOST, DASHBOARD_PORT)
        try:
            await server.serve()
        except asyncio.CancelledError:
            server.should_exit = True

    async def _active_forward_loop(self) -> None:
        """Proactively forward operator context to relevant agents using LLM routing."""
        _forwarded_ids: set[int] = set()  # Track already-forwarded messages

        while self._running:
            try:
                from shared.db import fetch_all
                recent_ops = await fetch_all(
                    """SELECT id, payload, created_at FROM events
                       WHERE event_type IN ('operator_message', 'telegram_message')
                       AND created_at > NOW() - INTERVAL '5 minutes'
                       ORDER BY created_at DESC LIMIT 5"""
                )
                for msg in recent_ops or []:
                    msg_id = msg.get("id", 0)
                    if msg_id in _forwarded_ids:
                        continue

                    payload = msg.get("payload", {})
                    if isinstance(payload, str):
                        import json
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            logger.warning("Malformed JSON payload in event %s, skipping", msg.get("id"))
                            continue
                    text = str(payload.get("text", payload.get("message", "")))
                    if not text:
                        continue

                    # Use LLM to decide who needs this context
                    try:
                        from shared.llm_client import llm
                        routing = await llm.generate(
                            f"Operator message: \"{text}\"\n\n"
                            f"Who needs this? Reply with ONLY one of: titan, clawdbot, both, neither",
                            model="fast", max_tokens=10, temperature=0.1,
                            use_dna=True, daemon_name="hermes",
                            operation="hermes.route_operator_message")
                        routing = routing.strip().lower()
                    except Exception as e:
                        logger.debug("LLM routing failed: %s", e)
                        # Fallback to keyword matching
                        targets = set()
                        if any(kw in text.lower() for kw in ("lead", "pipeline", "email", "deal", "revenue")):
                            targets.add("titan")
                        if any(kw in text.lower() for kw in ("skill", "scrape", "browser", "site", "build")):
                            targets.add("clawdbot")
                        routing = "both" if len(targets) == 2 else targets.pop() if targets else ""

                    from shared.comms import ask_agent
                    if routing in ("titan", "both"):
                        try:
                            await ask_agent("hermes", "titan",
                                f"Operator context for you: {text[:500]}", timeout=10)
                        except Exception as e:
                            logger.debug("Titan keyword matching failed: %s", e)
                    if routing in ("clawdbot", "both"):
                        try:
                            await ask_agent("hermes", "clawdbot",
                                f"Operator context for you: {text[:500]}", timeout=10)
                        except Exception as e:
                            logger.debug("ClawdBot keyword matching failed: %s", e)

                    _forwarded_ids.add(msg_id)
                    # Keep set bounded
                    if len(_forwarded_ids) > 100:
                        _forwarded_ids = set(list(_forwarded_ids)[-50:])

                # Check for stale pending approvals (>2 hours)
                stale_approvals = await fetch_all(
                    """SELECT * FROM events
                       WHERE event_type = 'review_item_created'
                       AND created_at < NOW() - INTERVAL '2 hours'
                       AND created_at > NOW() - INTERVAL '4 hours'
                       LIMIT 3"""
                )
                if stale_approvals:
                    from shared.comms import send_alert
                    await send_alert(
                        f"{len(stale_approvals)} approval(s) pending for over 2 hours. Check /review.",
                        sender="hermes",
                    )
            except Exception as e:
                logger.debug(f"Active forward loop: {e}")

            await asyncio.sleep(30)

    async def _alert_loop(self):
        """Standalone alert loop when Telegram is not configured."""
        while self._running:
            self.begin_work("loop:alerts")
            try:
                await dispatch_alerts()
            finally:
                self.finish_work("loop:alerts")
            await asyncio.sleep(self._alert_interval)

    async def stop(self):
        """Gracefully stop Hermes."""
        logger.info("Hermes shutdown requested...")
        self.request_shutdown()
        await self.wait_for_work_drain()
        await self.wait_until_stopped()
        logger.info("Hermes stopped.")

    async def health_check(self) -> dict:
        return {
            "agent": self.name,
            "status": "running" if self._running else "stopped",
            "telegram": bool(config.telegram.bot_token),
        }


async def main():
    hermes = HermesDaemon()

    loop = asyncio.get_event_loop()
    install_asyncio_exception_handler(loop, "hermes")
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(hermes.stop()))

    try:
        await hermes.start()
    except Exception as exc:
        capture_exception(exc, service_name="hermes", category="main")
        logger.exception("Hermes crashed")
        raise


async def main_with_a2a():
    """Entry point for Hermes daemon + A2A server."""
    import uvicorn as _uvicorn

    from hermes.a2a_server import create_hermes_a2a

    hermes = HermesDaemon()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(hermes.stop()))

    a2a_app = create_hermes_a2a(hermes)
    a2a_port = int(os.getenv("HERMES_A2A_PORT", "9002"))
    uvi_config = _uvicorn.Config(a2a_app, host="0.0.0.0", port=a2a_port, log_level="warning")
    server = _uvicorn.Server(uvi_config)

    logger.info("Hermes A2A server starting on :%d", a2a_port)
    await asyncio.gather(hermes.start(), server.serve())


if __name__ == "__main__":
    if os.getenv("HERMES_A2A", "1") == "1":
        asyncio.run(main_with_a2a())
    else:
        asyncio.run(main())
