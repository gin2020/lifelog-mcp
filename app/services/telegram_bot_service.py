"""Telegram Bot API client used only for notification delivery and bot commands."""

from dataclasses import dataclass
import logging

import httpx

from app.config.settings import Settings, get_settings


logger = logging.getLogger(__name__)


class TelegramBotConfigurationError(RuntimeError):
    """Raised when the Telegram Bot API settings are unavailable."""


@dataclass(frozen=True)
class TelegramBotApiError(RuntimeError):
    """Telegram Bot API failure with enough data to select retry behaviour."""

    status_code: int
    description: str
    retry_after: int | None = None


class TelegramBotService:
    """Send messages through the Telegram Bot API without involving OIDC."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the client with Bot API configuration."""
        self._settings = settings or get_settings()

    async def send_message(self, chat_id: int, text: str) -> None:
        """Deliver a text message to a subscribed Telegram private chat."""
        token = self._settings.telegram_bot_token
        if token is None:
            raise TelegramBotConfigurationError("TELEGRAM_BOT_TOKEN is not configured")

        url = f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json={"chat_id": chat_id, "text": text})
        except httpx.HTTPError as error:
            raise TelegramBotApiError(0, "Telegram request failed") from error

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.is_success and body.get("ok") is True:
            return

        parameters = body.get("parameters") if isinstance(body, dict) else None
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        raise TelegramBotApiError(
            response.status_code,
            str(body.get("description", "Telegram request failed")),
            retry_after if isinstance(retry_after, int) else None,
        )
