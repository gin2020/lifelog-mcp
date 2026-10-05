"""Staged MTProto user authorization and account persistence."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.telegram_user import TelegramAccount, TelegramAuthFlow
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApi, TelegramUserApiError


class TelegramUserAuthError(RuntimeError):
    """Raised for safe, user-facing authorization failures."""


class TelegramUserAuthService:
    """Persist only encrypted sessions while keeping codes/passwords transient."""

    def __init__(self, settings: Settings | None = None, api: TelegramUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or TelegramUserApi(self._settings)
        self._crypto = TelegramSessionCrypto(self._settings)

    async def start(self, user_id: int, phone: str) -> dict[str, str]:
        if not phone.strip():
            raise TelegramUserAuthError("Phone number is required")
        now = datetime.now(timezone.utc)
        flow_id = uuid4()
        client = self._api.client()
        try:
            await client.connect()
            await client.send_code_request(phone.strip())
            session = client.session.save()
        except Exception as error:
            raise TelegramUserAuthError("Telegram authorization code could not be requested") from error
        finally:
            await client.disconnect()
        with SessionLocal() as db:
            db.query(TelegramAuthFlow).filter(
                TelegramAuthFlow.user_id == user_id,
                TelegramAuthFlow.expires_at > now,
            ).update({TelegramAuthFlow.status: "cancelled"})
            db.add(TelegramAuthFlow(
                id=flow_id,
                user_id=user_id,
                phone=phone.strip(),
                session_ciphertext=self._crypto.encrypt(session),
                status="code_required",
                expires_at=now + timedelta(seconds=self._settings.telegram_user_auth_flow_ttl_seconds),
            ))
            db.commit()
        return {"flow_id": str(flow_id), "status": "code_required"}

    async def submit_code(self, user_id: int, flow_id: UUID, code: str) -> dict[str, str]:
        flow = self._load_flow(user_id, flow_id)
        client = self._api.client(self._crypto.decrypt(flow.session_ciphertext or ""))
        try:
            await client.connect()
            try:
                await client.sign_in(phone=flow.phone, code=code)
            except Exception as error:
                if error.__class__.__name__ != "SessionPasswordNeededError":
                    raise
                flow_status = "password_required"
                session = client.session.save()
                self._update_flow(flow_id, flow_status, self._crypto.encrypt(session))
                return {"flow_id": str(flow_id), "status": flow_status}
            return await self._finish(user_id, flow_id, client)
        except TelegramUserAuthError:
            raise
        except Exception as error:
            self._increment_attempt(flow_id)
            raise TelegramUserAuthError("Telegram authorization code was rejected") from error
        finally:
            await client.disconnect()

    async def submit_2fa(self, user_id: int, flow_id: UUID, password: str) -> dict[str, str]:
        flow = self._load_flow(user_id, flow_id)
        if flow.status != "password_required":
            raise TelegramUserAuthError("This authorization flow does not require a password")
        client = self._api.client(self._crypto.decrypt(flow.session_ciphertext or ""))
        try:
            await client.connect()
            await client.sign_in(password=password)
            return await self._finish(user_id, flow_id, client)
        except Exception as error:
            self._increment_attempt(flow_id)
            raise TelegramUserAuthError("Telegram two-factor authentication failed") from error
        finally:
            await client.disconnect()

    def cancel(self, user_id: int, flow_id: UUID) -> bool:
        with SessionLocal() as db:
            flow = db.scalar(select(TelegramAuthFlow).where(TelegramAuthFlow.id == flow_id, TelegramAuthFlow.user_id == user_id))
            if flow is None or flow.status in {"cancelled", "completed"}:
                return False
            flow.status = "cancelled"
            flow.session_ciphertext = None
            db.commit()
            return True

    def _load_flow(self, user_id: int, flow_id: UUID) -> TelegramAuthFlow:
        with SessionLocal() as db:
            flow = db.scalar(select(TelegramAuthFlow).where(TelegramAuthFlow.id == flow_id, TelegramAuthFlow.user_id == user_id))
            if flow is None or flow.expires_at <= datetime.now(timezone.utc) or flow.status in {"cancelled", "completed"}:
                raise TelegramUserAuthError("Telegram authorization flow is invalid or expired")
            if flow.attempts >= 5:
                raise TelegramUserAuthError("Too many Telegram authorization attempts")
            db.expunge(flow)
            return flow

    def _update_flow(self, flow_id: UUID, status: str, session_ciphertext: str) -> None:
        with SessionLocal() as db:
            flow = db.get(TelegramAuthFlow, flow_id)
            if flow is not None:
                flow.status = status
                flow.session_ciphertext = session_ciphertext
                db.commit()

    def _increment_attempt(self, flow_id: UUID) -> None:
        with SessionLocal() as db:
            flow = db.get(TelegramAuthFlow, flow_id)
            if flow is not None:
                flow.attempts += 1
                db.commit()

    async def _finish(self, user_id: int, flow_id: UUID, client) -> dict[str, str]:
        me = await client.get_me()
        telegram_user_id = getattr(me, "id", None)
        if not isinstance(telegram_user_id, int):
            raise TelegramUserAuthError("Telegram account identity was not returned")
        first = getattr(me, "first_name", None) or ""
        last = getattr(me, "last_name", None) or ""
        display_name = " ".join(part for part in (first, last) if part) or str(telegram_user_id)
        session_ciphertext = self._crypto.encrypt(client.session.save())
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id).with_for_update())
            if account is None:
                account = TelegramAccount(user_id=user_id)
                db.add(account)
            account.telegram_user_id = telegram_user_id
            account.username = getattr(me, "username", None)
            account.display_name = display_name
            account.session_ciphertext = session_ciphertext
            account.status = "active"
            account.last_connected_at = now
            account.last_error = None
            flow = db.get(TelegramAuthFlow, flow_id)
            if flow is not None:
                flow.status = "completed"
                flow.session_ciphertext = None
            db.commit()
        return {"flow_id": str(flow_id), "status": "connected", "telegram_user_id": str(telegram_user_id), "display_name": display_name}

    def status(self, user_id: int) -> dict[str, object]:
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id))
            if account is None:
                return {"connected": False, "status": "not_connected"}
            return {"connected": account.status == "active", "status": account.status, "telegram_user_id": account.telegram_user_id, "username": account.username, "display_name": account.display_name, "last_connected_at": account.last_connected_at.isoformat() if account.last_connected_at else None, "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None}

    def disconnect(self, user_id: int) -> bool:
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id).with_for_update())
            if account is None:
                return False
            account.status = "revoked"
            account.session_ciphertext = None
            db.commit()
            return True
