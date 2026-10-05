"""Small Telethon adapter for per-user Telegram MTProto sessions."""

from dataclasses import dataclass
from typing import Any, Callable

from app.config.settings import Settings, get_settings


class TelegramUserApiError(RuntimeError):
    """Safe application error for Telegram User API operations."""


@dataclass(frozen=True)
class TelegramPeer:
    peer_type: str
    telegram_peer_id: int
    access_hash: int | None
    username: str | None
    display_name: str


class TelegramUserApi:
    """Create short-lived Telethon clients; caller owns and closes each client."""

    def __init__(self, settings: Settings | None = None, client_factory: Callable[..., Any] | None = None) -> None:
        self._settings = settings or get_settings()
        self._client_factory = client_factory

    def _factory(self):
        if self._client_factory is not None:
            return self._client_factory
        try:
            from telethon import TelegramClient
        except ImportError as error:
            raise TelegramUserApiError("Telethon is not installed") from error
        return TelegramClient

    def _credentials(self) -> tuple[int, str]:
        if self._settings.telegram_api_id is None or self._settings.telegram_api_hash is None:
            raise TelegramUserApiError("Telegram User API credentials are not configured")
        return self._settings.telegram_api_id, self._settings.telegram_api_hash.get_secret_value()

    def client(self, session: str | None = None) -> Any:
        api_id, api_hash = self._credentials()
        try:
            from telethon.sessions import StringSession
        except ImportError as error:
            raise TelegramUserApiError("Telethon StringSession is unavailable") from error
        return self._factory()(StringSession(session), api_id, api_hash)

    @staticmethod
    def peer_from_entity(entity: Any) -> TelegramPeer:
        entity_id = getattr(entity, "id", None)
        if not isinstance(entity_id, int):
            raise TelegramUserApiError("Telegram entity has no numeric ID")
        if getattr(entity, "broadcast", False):
            peer_type = "channel"
        elif getattr(entity, "megagroup", False) or entity.__class__.__name__.lower().endswith("chat"):
            peer_type = "chat"
        else:
            peer_type = "user"
        first = getattr(entity, "first_name", None) or ""
        last = getattr(entity, "last_name", None) or ""
        title = getattr(entity, "title", None)
        display_name = str(title or " ".join(part for part in (first, last) if part) or getattr(entity, "username", None) or entity_id)
        return TelegramPeer(peer_type, entity_id, getattr(entity, "access_hash", None), getattr(entity, "username", None), display_name)

    async def resolve_peer(self, client: Any, reference: str | int) -> TelegramPeer:
        try:
            entity = await client.get_entity(reference)
        except Exception as error:
            raise TelegramUserApiError("Telegram peer could not be resolved") from error
        return self.peer_from_entity(entity)

    @staticmethod
    async def send_message(client: Any, peer: int, text: str, reply_to: int | None = None) -> int:
        try:
            message = await client.send_message(peer, text, reply_to=reply_to)
        except Exception as error:
            raise TelegramUserApiError("Telegram message could not be sent") from error
        message_id = getattr(message, "id", None)
        if not isinstance(message_id, int):
            raise TelegramUserApiError("Telegram did not return a message ID")
        return message_id

    @staticmethod
    async def get_messages(client: Any, peer: int, limit: int, min_id: int | None = None) -> list[dict[str, Any]]:
        try:
            messages = await client.get_messages(peer, limit=limit, min_id=min_id or 0)
        except Exception as error:
            raise TelegramUserApiError("Telegram messages could not be loaded") from error
        result = []
        for message in messages:
            result.append({
                "id": getattr(message, "id", None),
                "date": getattr(message, "date", None),
                "text": getattr(message, "message", None) or "",
                "sender_id": getattr(message, "sender_id", None),
                "out": bool(getattr(message, "out", False)),
            })
        return result
