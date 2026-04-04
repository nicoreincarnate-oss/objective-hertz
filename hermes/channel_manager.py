"""Unified channel manager for Hermes messaging.

Wraps OpenJarvis channels into a single interface that Hermes agents
can use to send messages across any platform.

Priority channels: Telegram, Email, Slack, WhatsApp
All 27 OJ channels registered as available for future activation.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Environment variable mapping: channel name -> list of env vars that must be set
_CHANNEL_CREDS: dict[str, list[str]] = {
    "telegram": ["TELEGRAM_BOT_TOKEN"],
    "email": ["EMAIL_USERNAME", "EMAIL_PASSWORD"],
    "slack": ["SLACK_BOT_TOKEN"],
    "whatsapp": ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"],
}

# Severity -> ordered list of channels to try
_ALERT_ROUTING: dict[str, list[str]] = {
    "critical": ["telegram", "email", "slack", "whatsapp"],
    "warning": ["telegram", "email"],
    "info": ["telegram"],
}


class ChannelManager:
    """Manages all messaging channels for Hermes.

    Auto-discovers OpenJarvis channels at initialization and provides a
    unified ``send()`` interface.  Falls back to the existing Hermes alert
    system (``hermes.alerts.send_operator_message``) when OJ channels are
    unavailable.
    """

    def __init__(self) -> None:
        self._channels: dict[str, Any] = {}  # name -> BaseChannel instance
        self._initialized: bool = False
        self._oj_available: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize available channels based on env vars.

        Imports the OJ channel modules and instantiates channels whose
        required credentials are present in the environment.  Channels
        that fail to connect are silently skipped.
        """
        if self._initialized:
            return

        try:
            # Importing the package triggers @ChannelRegistry.register()
            # for all 27 built-in channels.
            from openjarvis.channels._stubs import ChannelStatus  # noqa: F401
            from openjarvis.core.registry import ChannelRegistry

            self._oj_available = True
        except ImportError:
            logger.warning(
                "OpenJarvis channels not importable; "
                "falling back to legacy Hermes alerts"
            )
            self._initialized = True
            return

        for name, required_vars in _CHANNEL_CREDS.items():
            if not all(os.environ.get(v) for v in required_vars):
                logger.debug(
                    "Skipping channel %s: missing env vars %s",
                    name,
                    [v for v in required_vars if not os.environ.get(v)],
                )
                continue

            try:
                channel_cls = ChannelRegistry.get(name)
            except KeyError:
                logger.debug("Channel %s not in OJ registry", name)
                continue

            try:
                instance = channel_cls()
                await asyncio.to_thread(instance.connect)
                from openjarvis.channels._stubs import ChannelStatus

                if instance.status() == ChannelStatus.ERROR:
                    logger.warning("Channel %s connected with ERROR status", name)
                    continue

                self._channels[name] = instance
                logger.info("Channel initialized: %s", name)
            except Exception:
                logger.warning(
                    "Failed to initialize channel %s", name, exc_info=True
                )

        self._initialized = True
        logger.info(
            "ChannelManager ready: %d channel(s) active %s",
            len(self._channels),
            list(self._channels.keys()),
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        channel: str,
        target: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Send a message on any available channel.

        Args:
            channel: ``"telegram"``, ``"email"``, ``"slack"``, ``"whatsapp"``, etc.
            target: chat_id, email address, channel name, phone number.
            message: Text to send.
            metadata: Optional extras (``subject`` for email, ``thread_ts``
                for slack, etc.).

        Returns:
            ``True`` if the message was delivered successfully.
        """
        if not self._initialized:
            await self.initialize()

        ch = self._channels.get(channel)
        if ch is None:
            # Fallback to legacy Hermes alerts for unconfigured channels
            return await self._fallback_send(message)

        try:
            ok = await asyncio.to_thread(
                ch.send,
                target,
                message,
                conversation_id=metadata.get("conversation_id", "") if metadata else "",
                metadata=metadata,
            )
            if ok:
                logger.info(
                    "Message sent via %s to %s (%d chars)",
                    channel,
                    target,
                    len(message),
                )
            else:
                logger.warning("Channel %s send returned False for target %s", channel, target)
            return bool(ok)
        except Exception:
            logger.warning("Channel %s send raised an exception", channel, exc_info=True)
            return False

    async def send_alert(
        self,
        message: str,
        severity: str = "info",
    ) -> bool:
        """Send an alert to the operator on the best available channel.

        Routing rules by severity:
        - ``critical`` -- broadcast to **all** available channels
        - ``warning``  -- telegram + email
        - ``info``     -- telegram only

        Returns ``True`` if at least one channel delivered successfully.
        """
        if not self._initialized:
            await self.initialize()

        channels_to_try = _ALERT_ROUTING.get(severity, _ALERT_ROUTING["info"])

        any_sent = False
        for ch_name in channels_to_try:
            ch = self._channels.get(ch_name)
            if ch is None:
                continue

            # Resolve a default target for alert delivery
            target = self._resolve_alert_target(ch_name)
            if not target:
                logger.debug("No default target for alert channel %s", ch_name)
                continue

            try:
                ok = await asyncio.to_thread(
                    ch.send,
                    target,
                    message,
                    conversation_id="",
                    metadata={"subject": f"[Perseus Alert] {severity.upper()}"} if ch_name == "email" else None,
                )
                if ok:
                    logger.info("Alert sent via %s (severity=%s)", ch_name, severity)
                    any_sent = True
            except Exception:
                logger.warning("Alert via %s failed", ch_name, exc_info=True)

        # If no OJ channel succeeded, fall back to legacy
        if not any_sent:
            return await self._fallback_send(message)

        return any_sent

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def available_channels(self) -> list[str]:
        """List all initialized channel names."""
        return list(self._channels.keys())

    def channel_status(self) -> dict[str, str]:
        """Get status of all channels (connected/disconnected/error)."""
        result: dict[str, str] = {}
        for name, ch in self._channels.items():
            try:
                result[name] = ch.status().value
            except Exception:
                result[name] = "error"
        return result

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Disconnect all channels gracefully."""
        for name, ch in self._channels.items():
            try:
                await asyncio.to_thread(ch.disconnect)
                logger.info("Channel disconnected: %s", name)
            except Exception:
                logger.warning("Error disconnecting channel %s", name, exc_info=True)
        self._channels.clear()
        self._initialized = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_alert_target(channel_name: str) -> str:
        """Resolve the default operator target for a given channel."""
        env_map: dict[str, list[str]] = {
            "telegram": ["TELEGRAM_CHAT_ID", "OPENJARVIS_CHANNEL_TARGET"],
            "email": ["EMAIL_TO", "EMAIL_RECIPIENT", "EMAIL_USERNAME"],
            "slack": ["SLACK_CHANNEL_ID", "SLACK_DEFAULT_CHANNEL"],
            "whatsapp": ["WHATSAPP_TO"],
        }
        for env_var in env_map.get(channel_name, []):
            value = os.environ.get(env_var, "").strip()
            if value:
                return value
        return ""

    @staticmethod
    async def _fallback_send(message: str) -> bool:
        """Send via legacy Hermes alert system."""
        try:
            from hermes.alerts import send_operator_message

            result = await send_operator_message(message)
            return bool(result.get("sent"))
        except Exception:
            logger.warning("Fallback alert send also failed", exc_info=True)
            return False
