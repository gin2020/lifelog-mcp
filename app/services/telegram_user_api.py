"""Small Telethon adapter for per-user Telegram MTProto sessions."""

from dataclasses import dataclass
import re
from typing import Any, Callable

from app.config.settings import Settings, get_settings
from telethon import functions

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
        "AuthTokenExpiredError": ("qr_expired", "The QR login expired. Start a new QR connection flow."),
        "AuthTokenAlreadyAcceptedError": ("qr_already_used", "The QR login was already accepted. Start a new QR connection flow."),
        "AuthTokenInvalidError": ("qr_invalid", "The QR login token is invalid. Start a new QR connection flow."),
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
    phone: str | None = None
    is_contact: bool = False


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
    def sent_code_metadata(sent_code: Any) -> dict[str, str | int | bool | None]:
        """Return safe metadata from Telethon's auth.SentCode response."""
        sent_code_type = TelegramUserApi._type_name(getattr(sent_code, "type", None))
        next_type = TelegramUserApi._type_name(getattr(sent_code, "next_type", None))
        timeout = getattr(sent_code, "timeout", None)
        return {
            "delivery": {
                "SentCodeTypeApp": "telegram_app",
                "SentCodeTypeSms": "sms",
                "SentCodeTypeCall": "phone_call",
                "SentCodeTypeFlashCall": "flash_call",
            }.get(sent_code_type, "telegram_unknown"),
            "telegram_sent_code_type": sent_code_type,
            "telegram_next_type": next_type,
            "telegram_timeout": timeout if isinstance(timeout, int) else None,
            "phone_code_hash_present": bool(getattr(sent_code, "phone_code_hash", None)),
        }

    @staticmethod
    async def qr_login(client: Any) -> Any:
        """Create an official Telethon QR login state on a connected client."""
        try:
            return await client.qr_login()
        except Exception as error:
            raise translate_telegram_error(error, "starting QR authorization") from error

    @staticmethod
    def _type_name(value: Any) -> str | None:
        return value.__class__.__name__ if value is not None else None

    @staticmethod
    def peer_from_entity(entity: Any, is_contact: bool | None = None) -> TelegramPeer:
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
        phone = TelegramUserApi._format_phone(getattr(entity, "phone", None))
        contact = getattr(entity, "contact", None) if is_contact is None else is_contact
        return TelegramPeer(
            peer_type,
            entity_id,
            getattr(entity, "access_hash", None),
            getattr(entity, "username", None),
            display_name,
            phone=phone,
            is_contact=bool(contact),
        )

    async def resolve_peer(self, client: Any, reference: str | int) -> TelegramPeer:
        try:
            entity = await client.get_entity(reference)
        except Exception as error:
            if isinstance(reference, int):
                try:
                    entity = next(
                        contact for contact in await self._get_contacts(client)
                        if getattr(contact, "id", None) == reference
                    )
                except (StopIteration, TelegramUserApiError):
                    raise TelegramUserApiError("Telegram peer could not be resolved") from error
            else:
                raise TelegramUserApiError("Telegram peer could not be resolved") from error
        return self.peer_from_entity(entity)

    async def search_contacts(self, client: Any, query: str) -> list[TelegramPeer]:
        """Search the connected user's contacts or resolve an exact peer.

        Phone numbers and names are deliberately searched only in Telegram's
        contacts returned by ``contacts.getContacts``. Username and numeric
        ID queries use Telethon's normal entity resolver and never add a peer
        to the allowlist by themselves.
        """
        query = query.strip()
        if not query:
            raise TelegramUserApiError("Telegram contact search query is required")

        if query.lstrip("-").isdigit():
            try:
                return [await self.resolve_peer(client, int(query))]
            except TelegramUserApiError:
                normalized_query = self._normalize_phone(query)
                contacts = await self._get_contacts(client)
                return self._dedupe_peers(
                    self.peer_from_entity(contact, is_contact=True)
                    for contact in contacts
                    if self._normalize_phone(getattr(contact, "phone", None)) == normalized_query
                )

        if query.startswith("+"):
            normalized_query = self._normalize_phone(query)
            if not normalized_query:
                return []
            contacts = await self._get_contacts(client)
            return self._dedupe_peers(
                self.peer_from_entity(contact, is_contact=True)
                for contact in contacts
                if self._normalize_phone(getattr(contact, "phone", None)) == normalized_query
            )

        if query.startswith("@"):
            try:
                return [await self.resolve_peer(client, query)]
            except TelegramUserApiError:
                return []

        # A username without '@' is an exact identifier when Telegram resolves
        # it. If it does not, treat the same query as a contact-name search.
        if not any(character.isspace() for character in query):
            try:
                return [await self.resolve_peer(client, query)]
            except TelegramUserApiError:
                pass

        contacts = await self._get_contacts(client)
        query_folded = query.casefold()
        return self._dedupe_peers(
            self.peer_from_entity(contact, is_contact=True)
            for contact in contacts
            if self._contact_matches(contact, query_folded)
        )

    @staticmethod
    async def _get_contacts(client: Any) -> list[Any]:
        try:
            response = await client(functions.contacts.GetContactsRequest(0))
        except Exception as error:
            raise TelegramUserApiError("Telegram contacts could not be loaded") from error
        return list(getattr(response, "users", ()))

    @staticmethod
    def _contact_matches(entity: Any, query: str) -> bool:
        values = (
            getattr(entity, "first_name", None),
            getattr(entity, "last_name", None),
            getattr(entity, "username", None),
            " ".join(
                part for part in (
                    getattr(entity, "first_name", None),
                    getattr(entity, "last_name", None),
                )
                if isinstance(part, str) and part
            ),
        )
        return any(isinstance(value, str) and query in value.casefold() for value in values)

    @staticmethod
    def _dedupe_peers(peers) -> list[TelegramPeer]:
        result: dict[tuple[str, int], TelegramPeer] = {}
        for peer in peers:
            result.setdefault((peer.peer_type, peer.telegram_peer_id), peer)
        return list(result.values())

    @staticmethod
    def _normalize_phone(value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\D", "", str(value))

    @staticmethod
    def _format_phone(value: Any) -> str | None:
        normalized = TelegramUserApi._normalize_phone(value)
        return f"+{normalized}" if normalized else None

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
