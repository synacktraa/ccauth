"""Slack notifier for human intervention requests."""

from __future__ import annotations

import httpx

from ccauth.handoff.notifiers.base import Notifier, UrgencyLevel

URGENCY_CONFIG = {
    "critical": {"color": "#ff0000", "emoji": "\U0001f6a8"},  # Red, police light
    "normal": {"color": "#ffa500", "emoji": "\u26a0\ufe0f"},  # Orange, warning
    "low": {"color": "#808080", "emoji": "\u2139\ufe0f"},  # Gray, info
}


class SlackNotifier(Notifier):
    """Send intervention notifications to Slack using webhooks or Bot API.

    Supports two methods:
    1. Webhook URL (simpler, no token needed)
    2. Bot token + channel (more flexible, requires bot setup)
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        bot_token: str | None = None,
        channel: str | None = None,
    ):
        """Initialize Slack notifier.

        Args:
            webhook_url: Slack incoming webhook URL (preferred method).
            bot_token: Slack bot token (alternative to webhook).
            channel: Slack channel ID or name (required if using bot_token).

        Raises:
            ValueError: If neither webhook_url nor (bot_token + channel) provided.
        """
        if webhook_url:
            self.webhook_url = webhook_url
            self.bot_token = None
            self.channel = None
        elif bot_token and channel:
            self.webhook_url = None
            self.bot_token = bot_token
            self.channel = channel
        else:
            raise ValueError("Must provide either webhook_url or (bot_token + channel)")

    async def send(
        self,
        title: str,
        message: str,
        urgency: UrgencyLevel = "normal",
    ) -> None:
        """Send notification to Slack.

        Args:
            title: Notification title.
            message: Message in markdown format.
            urgency: Urgency level (critical, normal, low).

        Raises:
            httpx.HTTPError: If Slack API request fails.
        """
        config = URGENCY_CONFIG.get(urgency, URGENCY_CONFIG["normal"])
        formatted_message = f"{config['emoji']} {title}\n\n{message}"

        if self.webhook_url:
            await self._send_via_webhook(formatted_message, config["color"])
        else:
            await self._send_via_bot_api(formatted_message, config["color"])

    async def _send_via_webhook(self, formatted_message: str, color: str) -> None:
        """Send message using incoming webhook."""
        payload = {
            "text": formatted_message,
            "attachments": [{"color": color, "text": ""}],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.webhook_url,  # type: ignore[arg-type]
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()

    async def _send_via_bot_api(self, formatted_message: str, color: str) -> None:
        """Send message using Bot API."""
        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "channel": self.channel,
            "text": formatted_message,
            "attachments": [{"color": color, "text": ""}],
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()

            data = response.json()
            if not data.get("ok"):
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Slack API error: {error}")
