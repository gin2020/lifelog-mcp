"""Small Telethon adapter for per-user Telegram MTProto sessions."""

from dataclasses import dataclass
from typing import Any, Callable

from app.config.settings import Settings, get_settings

class TelegramUserApiError(RuntimeError):
    """Safe application error for Telegram User API operations."""

    def __init__(self, message: str, *, code: str = "telegram_error", retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def translate_telegram_error(error: Exception, operation: str) -> TelegramUserApiError:
    """Convert Telethon RPC errors into safe, actionable application errors."""
    name = error.__class__.__name__
    retry_after = getattr(error, "seconds", None) if name == "FloodWaitError" else None
    messages = {
        "PhoneNumberInvalidError": ("phone_number_invalid", "Telegram rejected the phone number. Use international format, for example +15551234567."),
        "PhoneNumberBannedError": ("phone_number_banned", "This phone number is banned by Telegram."),
        "PhoneNumberUnoccupiedError": ("phone_number_not_registered", "This phone number is not registered in Telegram."),
        "PhoneNumberFloodError": ("phone_number_flood", "Too many login attempts were made for this phone number. Try again later."),
        "PhoneCodeInvalidError": ("phone_code_invalid", "The Telegram login code is invalid. Request a new code if needed."),
        "PhoneCodeExpiredError": ("phone_code_expired", "The Telegram login code expired. Start a new connection flow."),
        "PhoneCodeEmptyError": ("phone_code_empty", "The Telegram login code is empty."),
        "PhoneCodeHashEmptyError": ("phone_code_hash_missing", "The Telegram login flow lost its code state. Start a new connection flow."),
        "SessionPasswordNeededError": ("session_password_required", "Telegram requires the account's two-factor password."),
        "PasswordHashInvalidError": ("password_invalid", "The Telegram two-factor password is invalid."),
        "FloodWaitError": ("flood_wait", f"Telegram temporarily rate-limited this operation. Retry after {retry_after or 0} seconds."),
        "AuthKeyUnregisteredError": ("session_invalid", "The Telegram session is no longer valid. Start a new connection flow."),
        "AuthRestartError": ("auth_restart", "Telegram restarted authorization. Start a new connection flow."),
        "ApiIdInvalidError": ("api_id_invalid", "Telegram API ID/API hash are invalid."),
        "ApiIdPublishedFloodError": ("api_id_flood", "Telegram rejected this API application because of excessive API usage."),
        "UserDeactivatedBanError": ("user_deactivated", "The Telegram account is deactivated or banned."),
    }
    code, message = messages.get(name, ("telegram_error", f"Telegram authorization failed during {operation}."))
    return TelegramUserApiError(message, code=code, retry_after=retry_after)


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
