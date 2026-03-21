"""
Hermes Daemon — starts Telegram bot and alert dispatcher.
"""

import asyncio
import signal
import logging

from shared.config import config
from shared.logging_config import setup_logging
from shared import db
from shared.agent_base import AgentBase

from hermes.telegram_bot import create_bot
from hermes.alerts import dispatch_alerts, send_morning_briefing

logger = setup_logging("hermes")


class HermesDaemon(AgentBase):
    name = "hermes"
    description = "Intelligence & relationship layer — Telegram interface, alerts, briefings."

    def __init__(self):
        super().__init__()
        self._running = False
        self._alert_interval = 30  # Check for alerts every 30 seconds

    async def start(self):
        """Start Hermes — Telegram bot + alert dispatcher."""
        logger.info("Hermes starting up...")
        await db.init_pool()
        await self.register()
        self._running = True

        if not config.telegram.bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN not set — running alert dispatcher only")
            await self._alert_loop()
        else:
            # Run bot and alert dispatcher concurrently
            bot = create_bot()
            await bot.initialize()
            await bot.start()
            try:
                await bot.updater.start_polling()
                logger.info("Hermes Telegram bot is LIVE.")

                # Run alert loop alongside the bot
                while self._running:
                    await dispatch_alerts()
                    await asyncio.sleep(self._alert_interval)
            finally:
                await bot.updater.stop()
                await bot.stop()
                await bot.shutdown()

    async def _alert_loop(self):
        """Standalone alert loop when Telegram is not configured."""
        while self._running:
            await dispatch_alerts()
            await asyncio.sleep(self._alert_interval)

    async def stop(self):
        """Gracefully stop Hermes."""
        logger.info("Hermes shutting down...")
        self._running = False
        await self.deregister()
        await db.close_pool()
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
