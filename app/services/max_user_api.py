"""Small, lazy PyMax adapter for personal MAX Web sessions."""

from dataclasses import dataclass
import base64
from datetime import date, datetime
from enum import Enum
import json
import logging
import math
from pathlib import Path
import tempfile
from collections.abc import Mapping
from typing import Any, Callable
from uuid import UUID

from app.config.settings import Settings, get_settings


logger = logging.getLogger(__name__)


class MaxUserApiError(RuntimeError):
    """Safe application error for MAX user-client operations."""

    def __init__(self, message: str, *, code: str = "max_error") -> None:
        super().__init__(message)
        self.code = code


class MaxTwoFactorRequiredError(RuntimeError):
    """Raised instead of blocking on PyMax's interactive console provider."""


class MaxNonInteractivePasswordProvider:
    """Fail safely when MAX requests a password on a headless server."""

    async def get_password(self, hint: str | None = None) -> str:
        raise MaxTwoFactorRequiredError("MAX requested a 2FA password")


@dataclass
class MaxClientHandle:
    """A PyMax client and its private plaintext session workspace."""

    client: Any
    temp_dir: tempfile.TemporaryDirectory
    session_path: Path
    _closed: bool = False

    def read_session(self) -> bytes | None:
        try:
            return self.session_path.read_bytes() if self.session_path.exists() else None
        except OSError as error:
            raise MaxUserApiError("MAX session could not be read", code="session_read_failed") from error

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self.client, "close", None) or getattr(self.client, "stop", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
        finally:
            self.temp_dir.cleanup()


@dataclass(frozen=True)
class MaxChat:
    max_chat_id: int
    display_name: str
    chat_type: str | None
    max_user_id: int | None = None
    username: str | None = None
    phone: str | None = None
    profile_link: str | None = None
    participant_ids: tuple[int, ...] = ()
    contact_found: bool = False


class MaxUserApi:
    """Create short-lived PyMax clients; callers own and close each handle."""

    def __init__(self, settings: Settings | None = None, client_factory: Callable[..., Any] | None = None) -> None:
        self._settings = settings or get_settings()
        self._client_factory = client_factory

    def _factory(self):
        if self._client_factory is not None:
            return self._client_factory
        try:
            from pymax import WebClient
        except ImportError as error:
            raise MaxUserApiError(
                "PyMax is not installed; install the maxapi-python dependency",
                code="dependency_missing",
            ) from error
        return WebClient

    def _extra_config(self, device_id: str | None = None) -> Any:
        try:
            from pymax import ExtraConfig
        except ImportError as error:
            raise MaxUserApiError("PyMax is not installed", code="dependency_missing") from error
        kwargs: dict[str, Any] = {
            "persist_session": True,
            "reconnect": False,
            "relogin": False,
            "telemetry": False,
            "log_level": "WARNING",
        }
        if device_id:
            kwargs["device_id"] = device_id
        return ExtraConfig(**kwargs)

    def create_client(
        self,
        session_blob: bytes | None = None,
        *,
        device_id: str | None = None,
        qr_provider: Any | None = None,
    ) -> MaxClientHandle:
        temp_dir = tempfile.TemporaryDirectory(prefix="lifelog-max-")
        session_path = Path(temp_dir.name) / "session.db"
        try:
            if session_blob:
                session_path.write_bytes(session_blob)
                session_path.chmod(0o600)
            kwargs: dict[str, Any] = {
                "work_dir": temp_dir.name,
                "session_name": session_path.name,
                "extra_config": self._extra_config(device_id),
            }
            if qr_provider is not None:
                kwargs["qr_provider"] = qr_provider
                from pymax import QrAuthFlow

                kwargs["auth_flow"] = QrAuthFlow(
                    qr_provider,
                    password_provider=MaxNonInteractivePasswordProvider(),
                )
            client = self._factory()(**kwargs)
            return MaxClientHandle(client, temp_dir, session_path)
        except MaxUserApiError:
            temp_dir.cleanup()
            raise
        except Exception as error:
            temp_dir.cleanup()
            raise MaxUserApiError("MAX client could not be created", code="client_create_failed") from error

    @staticmethod
    async def connect(handle: MaxClientHandle) -> None:
        try:
            await handle.client.connect()
        except Exception as error:
            raise translate_max_error(error, "connecting the MAX client") from error

    @staticmethod
    async def start(handle: MaxClientHandle) -> None:
        try:
            await handle.client.start()
        except Exception as error:
            raise translate_max_error(error, "running the MAX client") from error

    @staticmethod
    async def get_chats(client: Any) -> list[MaxChat]:
        try:
            chats = await client.fetch_chats()
        except Exception as error:
            raise translate_max_error(error, "loading MAX chats") from error
        raw_chats = list(chats or [])
        user_ids = MaxUserApi.chat_user_ids(raw_chats)
        users = await MaxUserApi._load_users(client, user_ids)
        result: list[MaxChat] = []
        for chat in raw_chats:
            normalized = MaxUserApi.chat_from_object(chat, users=users, own_user_id=MaxUserApi.own_user_id(client))
            if normalized is not None:
                result.append(normalized)
        return result

    @staticmethod
    async def get_history(client: Any, chat_id: int, limit: int, after_message_id: int | None = None) -> list[dict[str, Any]]:
        try:
            messages = await client.fetch_history(
                chat_id=chat_id,
                backward=limit,
                get_messages=True,
                interactive=False,
            )
        except Exception as error:
            raise translate_max_error(error, "loading MAX message history") from error
        normalized = [MaxUserApi.message_from_object(message, chat_id) for message in messages or []]
        if after_message_id is not None:
            normalized = [message for message in normalized if isinstance(message.get("id"), int) and message["id"] > after_message_id]
        return normalized

    @staticmethod
    async def send_message(client: Any, chat_id: int, text: str, reply_to: int | None = None) -> int | None:
        try:
            message = await client.send_message(chat_id=chat_id, text=text, reply_to=reply_to)
        except Exception as error:
            raise translate_max_error(error, "sending a MAX message") from error
        normalized = MaxUserApi.message_from_object(message, chat_id) if message is not None else {}
        return normalized.get("id") if isinstance(normalized.get("id"), int) else None

    @staticmethod
    def profile_from_client(client: Any) -> dict[str, Any]:
        profile = getattr(client, "me", None)
        raw = MaxUserApi.as_dict(profile)
        contact = raw.get("contact") if isinstance(raw.get("contact"), dict) else raw
        normalized = MaxUserApi.user_from_object(contact or raw)
        return {
            "max_user_id": normalized.get("max_user_id"),
            "phone": normalized.get("phone"),
            "display_name": str(normalized.get("display_name") or normalized.get("max_user_id") or "MAX user"),
            "username": normalized.get("username"),
        }

    @staticmethod
    def chat_from_object(
        chat: Any,
        *,
        users: dict[int, dict[str, Any]] | None = None,
        own_user_id: int | None = None,
    ) -> MaxChat | None:
        raw = MaxUserApi.as_dict(chat)
        chat_id = MaxUserApi.numeric(raw.get("id")) or MaxUserApi.numeric(raw.get("chat_id"))
        if chat_id is None:
            return None
        participant_ids = MaxUserApi._participant_ids(raw)
        chat_type = MaxUserApi._string_value(raw.get("type"))
        peer_id = MaxUserApi._dialog_peer_id(raw, participant_ids, own_user_id)
        profile = (users or {}).get(peer_id) if peer_id is not None else None
        title = raw.get("title") or raw.get("name") or raw.get("display_name")
        if not title and profile:
            title = profile.get("display_name") or profile.get("username") or profile.get("phone")
        if not title:
            title = f"MAX chat {chat_id} (имя недоступно)"
        return MaxChat(
            max_chat_id=chat_id,
            display_name=str(title),
            chat_type=chat_type,
            max_user_id=peer_id,
            username=profile.get("username") if profile else None,
            phone=profile.get("phone") if profile else None,
            profile_link=profile.get("profile_link") if profile else None,
            participant_ids=tuple(participant_ids),
            contact_found=profile is not None,
        )

    @staticmethod
    def chat_user_ids(chats: list[Any]) -> list[int]:
        """Collect participant IDs once so contact enrichment is batched."""
        result: set[int] = set()
        for chat in chats:
            raw = MaxUserApi.as_dict(chat)
            result.update(MaxUserApi._participant_ids(raw))
            owner = MaxUserApi.numeric(raw.get("owner"))
            if owner is not None:
                result.add(owner)
        return sorted(result)

    @staticmethod
    async def _load_users(client: Any, user_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not user_ids:
            return {}
        users: dict[int, dict[str, Any]] = {}

        # ``get_users`` uses PyMax's cache and makes one request for all misses.
        # The fallbacks keep the adapter usable with older PyMax versions and
        # with small test doubles.
        get_users = getattr(client, "get_users", None) or getattr(client, "fetch_users", None)
        if callable(get_users):
            try:
                values = await get_users(user_ids)
            except Exception as error:
                logger.warning("MAX contact enrichment failed type=%s", error.__class__.__name__)
                values = []
            for value in values or []:
                normalized = MaxUserApi.user_from_object(value)
                if normalized.get("max_user_id") is not None:
                    users[normalized["max_user_id"]] = normalized

        for value in MaxUserApi._cached_user_values(client):
            normalized = MaxUserApi.user_from_object(value)
            if normalized.get("max_user_id") is not None:
                users.setdefault(normalized["max_user_id"], normalized)
        return users

    @staticmethod
    def _cached_user_values(client: Any) -> list[Any]:
        values: list[Any] = []
        contacts = getattr(client, "contacts", None)
        if isinstance(contacts, (list, tuple)):
            values.extend(value for value in contacts if value is not None)
        profile = getattr(client, "me", None)
        profile_raw = MaxUserApi.as_dict(profile)
        contact = profile_raw.get("contact") if isinstance(profile_raw.get("contact"), dict) else None
        if contact:
            values.append(contact)
        return values

    @staticmethod
    def user_from_object(user: Any) -> dict[str, Any]:
        raw = MaxUserApi.as_dict(user)
        user_id = MaxUserApi.numeric(raw.get("id")) or MaxUserApi.numeric(raw.get("user_id"))
        names = raw.get("names") or []
        display_name = raw.get("display_name") or raw.get("name")
        if not display_name:
            for name in names:
                name_raw = MaxUserApi.as_dict(name)
                display_name = name_raw.get("name") or " ".join(
                    part for part in (
                        name_raw.get("first_name") or name_raw.get("firstName"),
                        name_raw.get("last_name") or name_raw.get("lastName"),
                    ) if part
                )
                if display_name:
                    break
        username = raw.get("username") or raw.get("user_name") or raw.get("handle")
        if username is not None:
            username = str(username).lstrip("@")
        phone = raw.get("phone")
        profile_link = raw.get("link") or raw.get("profile_link")
        return {
            "max_user_id": user_id,
            "display_name": str(display_name) if display_name else None,
            "username": username,
            "phone": str(phone) if phone is not None else None,
            "profile_link": str(profile_link) if profile_link is not None else None,
        }

    @staticmethod
    def own_user_id(client: Any) -> int | None:
        profile = getattr(client, "me", None)
        raw = MaxUserApi.as_dict(profile)
        contact = raw.get("contact") if isinstance(raw.get("contact"), dict) else raw
        return MaxUserApi.numeric(contact.get("id")) or MaxUserApi.numeric(raw.get("id"))

    @staticmethod
    def _participant_ids(raw: dict[str, Any]) -> list[int]:
        participants = raw.get("participants") or {}
        values = participants.keys() if isinstance(participants, dict) else participants
        result = {value for value in (MaxUserApi.numeric(item) for item in values or []) if value is not None}
        owner = MaxUserApi.numeric(raw.get("owner"))
        if owner is not None:
            result.add(owner)
        return sorted(result)

    @staticmethod
    def _dialog_peer_id(raw: dict[str, Any], participant_ids: list[int], own_user_id: int | None) -> int | None:
        chat_type = MaxUserApi._string_value(raw.get("type"))
        if chat_type and chat_type.upper() not in {"DIALOG", "USER"}:
            return None
        candidates = [user_id for user_id in participant_ids if user_id != own_user_id]
        if candidates:
            return candidates[0]
        owner = MaxUserApi.numeric(raw.get("owner"))
        return owner if owner is not None and owner != own_user_id else None

    @staticmethod
    def _string_value(value: Any) -> str | None:
        if value is None:
            return None
        return str(value.value if hasattr(value, "value") else value)

    @staticmethod
    def message_from_object(message: Any, fallback_chat_id: int | None = None) -> dict[str, Any]:
        raw = MaxUserApi.as_dict(message)
        message_id = MaxUserApi.numeric(raw.get("id")) or MaxUserApi.numeric(raw.get("message_id"))
        chat_id = MaxUserApi.numeric(raw.get("chat_id")) or fallback_chat_id
        sender = raw.get("sender")
        sender_raw = MaxUserApi.as_dict(sender) if sender is not None else {}
        sender_id = MaxUserApi.numeric(sender) or MaxUserApi.numeric(sender_raw.get("id")) or MaxUserApi.numeric(raw.get("sender_id"))
        text = raw.get("text") or raw.get("body")
        return {
            "id": message_id,
            "chat_id": chat_id,
            "text": MaxUserApi._json_safe(text, key="text"),
            "sender_id": sender_id,
            "time": MaxUserApi._json_safe(
                raw.get("time") or raw.get("timestamp") or raw.get("created_at"),
                key="time",
            ),
            "out": bool(raw.get("out") or raw.get("is_out") or raw.get("from_me")),
            "raw": MaxUserApi._json_safe(raw),
        }

    _TEXT_BYTE_KEYS = frozenset({
        "body",
        "caption",
        "description",
        "file_name",
        "filename",
        "first_name",
        "last_name",
        "mime_type",
        "name",
        "text",
        "title",
        "username",
    })

    @staticmethod
    def _json_safe(value: Any, *, key: str | None = None) -> Any:
        """Return a JSON-compatible value without losing message records.

        Text-like fields are decoded as UTF-8 with replacement for malformed
        sequences. Bytes in all other fields are binary data and are kept as a
        reversible base64 marker instead of being guessed as text.
        """
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (bytes, bytearray, memoryview)):
            data = bytes(value)
            if key in MaxUserApi._TEXT_BYTE_KEYS:
                return data.decode("utf-8", errors="replace")
            return {
                "__lifelog_type": "bytes",
                "encoding": "base64",
                "data": base64.b64encode(data).decode("ascii"),
            }
        if isinstance(value, Enum):
            return MaxUserApi._json_safe(value.value, key=key)
        if isinstance(value, (datetime, date, UUID)):
            return value.isoformat() if hasattr(value, "isoformat") else str(value)
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                if isinstance(raw_key, str):
                    safe_key = raw_key
                elif isinstance(raw_key, (bytes, bytearray, memoryview)):
                    safe_key = bytes(raw_key).decode("utf-8", errors="replace")
                else:
                    safe_key = str(raw_key)
                result[safe_key] = MaxUserApi._json_safe(raw_value, key=safe_key)
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            return [MaxUserApi._json_safe(item, key=key) for item in value]
        for method_name in ("model_dump", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return MaxUserApi._json_safe(method(), key=key)
                except TypeError:
                    pass
        # Unknown optional objects must not make the complete MCP result
        # unserializable. Keep a diagnostic marker, never the object itself.
        return {"__lifelog_type": "unsupported", "class": value.__class__.__name__}

    @staticmethod
    def as_dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        for method_name in ("model_dump", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    result = method()
                    if isinstance(result, dict):
                        return result
                except TypeError:
                    pass
        try:
            return json.loads(json.dumps(value, default=lambda item: vars(item)))
        except (TypeError, ValueError):
            return {key: getattr(value, key) for key in dir(value) if not key.startswith("_") and not callable(getattr(value, key, None))}

    @staticmethod
    def numeric(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def translate_max_error(error: Exception, operation: str) -> MaxUserApiError:
    if isinstance(error, MaxUserApiError):
        return error
    if isinstance(error, MaxTwoFactorRequiredError):
        return MaxUserApiError(
            "MAX requires a 2FA password; headless QR authorization cannot continue",
            code="2fa_required",
        )
    if isinstance(error, (ConnectionError, EOFError, OSError)):
        return MaxUserApiError(f"MAX connection failed while {operation}", code="connection_error")
    if isinstance(error, (TimeoutError,)):
        return MaxUserApiError(f"MAX timed out while {operation}", code="timeout")
    name = error.__class__.__name__
    if name in {"InvalidTokenError", "AuthError", "LoginTokenInvalidError"}:
        return MaxUserApiError("The MAX session is no longer authorized", code="session_invalid")
    if name in {"QrExpiredError", "AuthTrackExpiredError"}:
        return MaxUserApiError("The MAX QR code expired; start a new authorization flow", code="qr_expired")
    if name == "RuntimeError" and str(error) == "QR authentication expired":
        return MaxUserApiError("The MAX QR code expired; start a new authorization flow", code="qr_expired")
    if name in {"ApiError", "PasswordAttemptsExceededError"}:
        return MaxUserApiError(f"MAX authorization failed while {operation}", code="auth_error")
    return MaxUserApiError(f"MAX operation failed while {operation}", code="max_error")
