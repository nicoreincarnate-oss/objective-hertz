"""
Hermes Daemon — starts Telegram bot, alert dispatcher, and web dashboard.
"""

import asyncio
import os
import signal
import logging

import uvicorn

from shared.config import config
from shared.logging_config import setup_logging
from shared import db
from shared.agent_base import AgentBase

from hermes.telegram_bot import create_bot
from hermes.alerts import dispatch_alerts, send_morning_briefing

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
        await self.register()
        self._stopped.clear()
        self._running = True

        # Start the web dashboard in a background task
        dashboard_task = asyncio.create_task(self._run_dashboard())

        try:
            _token = (config.telegram.bot_token or "").strip()
            _placeholder_tokens = {"", "CHANGE_ME", "your-bot-token-here"}
            if not _token or _token in _placeholder_tokens:
                logger.warning("TELEGRAM_BOT_TOKEN not set or placeholder — running dashboard + alerts only")
                await self._alert_loop()
            else:
                # Run bot and alert dispatcher concurrently
                bot = create_bot()
                try:
                    await bot.initialize()
                except Exception as e:
                    logger.warning("Telegram bot failed to initialize (%s) — running dashboard + alerts only", e)
                    await self._alert_loop()
                    return
                await bot.start()
                try:
                    await bot.updater.start_polling()
                    logger.info("Hermes Telegram bot is LIVE.")

                    # Run alert loop alongside the bot
                    while self._running:
                        self.begin_work("loop:alerts")
                        try:
                            await dispatch_alerts()
                        finally:
                            self.finish_work("loop:alerts")
                        await asyncio.sleep(self._alert_interval)
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
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(hermes.stop()))

    await hermes.start()


if __name__ == "__main__":
    asyncio.run(main())
