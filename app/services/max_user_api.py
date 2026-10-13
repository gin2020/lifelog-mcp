"""Small, lazy PyMax adapter for personal MAX Web sessions."""

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

from app.config.settings import Settings, get_settings


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
        result: list[MaxChat] = []
        for chat in chats or []:
            normalized = MaxUserApi.chat_from_object(chat)
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
        user_id = MaxUserApi.numeric(contact.get("id")) or MaxUserApi.numeric(raw.get("id"))
        names = contact.get("names") or raw.get("names") or []
        display_name = contact.get("display_name") or contact.get("name") or raw.get("display_name")
        if not display_name and names and isinstance(names[0], dict):
            display_name = names[0].get("name") or " ".join(
                part for part in (names[0].get("firstName"), names[0].get("lastName")) if part
            )
        return {
            "max_user_id": user_id,
            "phone": contact.get("phone") or raw.get("phone"),
            "display_name": str(display_name or user_id or "MAX user"),
            "username": contact.get("username") or raw.get("username"),
        }

    @staticmethod
    def chat_from_object(chat: Any) -> MaxChat | None:
        raw = MaxUserApi.as_dict(chat)
        chat_id = MaxUserApi.numeric(raw.get("id")) or MaxUserApi.numeric(raw.get("chat_id"))
        if chat_id is None:
            return None
        title = raw.get("title") or raw.get("name") or raw.get("display_name")
        if not title:
            title = f"MAX chat {chat_id}"
        return MaxChat(chat_id, str(title), raw.get("type"))

    @staticmethod
    def message_from_object(message: Any, fallback_chat_id: int | None = None) -> dict[str, Any]:
        raw = MaxUserApi.as_dict(message)
        message_id = MaxUserApi.numeric(raw.get("id")) or MaxUserApi.numeric(raw.get("message_id"))
        chat_id = MaxUserApi.numeric(raw.get("chat_id")) or fallback_chat_id
        sender = raw.get("sender")
        sender_raw = MaxUserApi.as_dict(sender) if sender is not None else {}
        sender_id = MaxUserApi.numeric(sender) or MaxUserApi.numeric(sender_raw.get("id")) or MaxUserApi.numeric(raw.get("sender_id"))
        return {
            "id": message_id,
            "chat_id": chat_id,
            "text": raw.get("text") or raw.get("body"),
            "sender_id": sender_id,
            "time": raw.get("time") or raw.get("timestamp") or raw.get("created_at"),
            "out": bool(raw.get("out") or raw.get("is_out") or raw.get("from_me")),
            "raw": raw,
        }

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
