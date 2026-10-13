"""Staged MTProto user authorization and account persistence."""

from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from telethon.errors import SessionPasswordNeededError

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.telegram_user import TelegramAccount, TelegramAuthFlow
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApi, TelegramUserApiError, translate_telegram_error
from app.services.telegram_qr_runtime import PreparedQrLogin, telegram_qr_runtime


logger = logging.getLogger(__name__)


class TelegramUserAuthError(RuntimeError):
    """Raised for safe, user-facing authorization failures."""

    def __init__(self, message: str, *, code: str = "telegram_auth_error", retry_after: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    def __str__(self) -> str:
        suffix = f" Retry after {self.retry_after} seconds." if self.retry_after is not None else ""
        return f"[{self.code}] {self.args[0]}{suffix}"


class TelegramUserAuthService:
    """Persist only encrypted sessions while keeping codes/passwords transient."""

    def __init__(self, settings: Settings | None = None, api: TelegramUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or TelegramUserApi(self._settings)
        self._crypto = TelegramSessionCrypto(self._settings)

    async def start(self, user_id: int, phone: str, account_id: int | None = None, account_label: str | None = None) -> dict[str, object]:
        if not phone.strip():
            raise TelegramUserAuthError("Phone number is required")
        now = datetime.now(timezone.utc)
        flow_id = uuid4()
        self._validate_target(user_id, account_id, account_label)
        await telegram_qr_runtime.cancel_user(user_id, account_id, account_label)
        client = self._api.client()
        try:
            await client.connect()
            sent_code = await client.send_code_request(phone.strip())
            phone_code_hash = getattr(sent_code, "phone_code_hash", None)
            if not isinstance(phone_code_hash, str) or not phone_code_hash:
                raise TelegramUserAuthError(
                    "Telegram did not return a phone code state. Start a new connection flow.",
                    code="phone_code_hash_missing",
                )
            session = client.session.save()
            sent_code_metadata = self._api.sent_code_metadata(sent_code)
            logger.info(
                "Telegram auth code request accepted: sent_code_type=%s next_type=%s timeout=%s phone_code_hash_present=%s",
                sent_code_metadata["telegram_sent_code_type"],
                sent_code_metadata["telegram_next_type"],
                sent_code_metadata["telegram_timeout"],
                sent_code_metadata["phone_code_hash_present"],
            )
        except TelegramUserAuthError:
            raise
        except TelegramUserApiError as error:
            logger.info("Telegram auth code request failed: rpc_error_class=%s", error.__cause__.__class__.__name__ if error.__cause__ else error.__class__.__name__)
            raise TelegramUserAuthError(str(error), code=error.code, retry_after=error.retry_after) from error
        except Exception as error:
            translated = translate_telegram_error(error, "requesting the login code")
            logger.info("Telegram auth code request failed: rpc_error_class=%s", error.__class__.__name__)
            raise TelegramUserAuthError(str(translated), code=translated.code, retry_after=translated.retry_after) from error
        finally:
            await client.disconnect()
        with SessionLocal() as db:
            db.query(TelegramAuthFlow).filter(
                TelegramAuthFlow.user_id == user_id,
                TelegramAuthFlow.expires_at > now,
                *self._flow_scope_filters(account_id, account_label),
            ).update({
                TelegramAuthFlow.status: "cancelled",
                TelegramAuthFlow.session_ciphertext: None,
                TelegramAuthFlow.phone_code_hash_ciphertext: None,
                TelegramAuthFlow.qr_expires_at: None,
            })
            db.add(TelegramAuthFlow(
                id=flow_id,
                user_id=user_id,
                account_id=account_id,
                account_label=account_label,
                phone=phone.strip(),
                session_ciphertext=self._crypto.encrypt(session),
                phone_code_hash_ciphertext=self._crypto.encrypt(phone_code_hash),
                status="code_required",
                expires_at=now + timedelta(seconds=self._settings.telegram_user_auth_flow_ttl_seconds),
            ))
            db.commit()
        return {"flow_id": str(flow_id), "status": "code_required", **sent_code_metadata}

    async def qr_start(self, user_id: int, account_id: int | None = None, account_label: str | None = None) -> dict[str, object]:
        """Start official Telegram QR authorization and return only its display URL."""
        now = datetime.now(timezone.utc)
        flow_id = uuid4()
        self._validate_target(user_id, account_id, account_label)
        await telegram_qr_runtime.cancel_user(user_id, account_id, account_label)
        prepared: PreparedQrLogin | None = None
        try:
            prepared = await telegram_qr_runtime.prepare(self._api)
        except TelegramUserAuthError:
            raise
        except TelegramUserApiError as error:
            logger.info("Telegram QR auth start failed: rpc_error_class=%s", error.__cause__.__class__.__name__ if error.__cause__ else error.__class__.__name__)
            raise TelegramUserAuthError(str(error), code=error.code, retry_after=error.retry_after) from error
        except Exception as error:
            translated = translate_telegram_error(error, "starting QR authorization")
            logger.info("Telegram QR auth start failed: rpc_error_class=%s", error.__class__.__name__)
            raise TelegramUserAuthError(str(translated), code=translated.code, retry_after=translated.retry_after) from error
        try:
            with SessionLocal() as db:
                db.query(TelegramAuthFlow).filter(
                    TelegramAuthFlow.user_id == user_id,
                    TelegramAuthFlow.expires_at > now,
                    *self._flow_scope_filters(account_id, account_label),
                ).update({
                    TelegramAuthFlow.status: "cancelled",
                    TelegramAuthFlow.session_ciphertext: None,
                    TelegramAuthFlow.phone_code_hash_ciphertext: None,
                    TelegramAuthFlow.qr_expires_at: None,
                })
                db.add(TelegramAuthFlow(
                    id=flow_id,
                    user_id=user_id,
                    account_id=account_id,
                    account_label=account_label,
                    phone=None,
                    session_ciphertext=self._crypto.encrypt(prepared.session),
                    qr_expires_at=prepared.expires_at,
                    status="qr_code_required",
                    expires_at=now + timedelta(seconds=self._settings.telegram_user_auth_flow_ttl_seconds),
                ))
                db.commit()
        except Exception:
            await prepared.client.disconnect()
            raise
        deadline = now + timedelta(seconds=self._settings.telegram_user_auth_flow_ttl_seconds)
        await telegram_qr_runtime.register(self, user_id, flow_id, prepared, deadline, account_id, account_label)
        logger.info("Telegram QR auth runtime created: user_id=%s flow_id=%s", user_id, flow_id)
        return {"flow_id": str(flow_id), "status": "qr_code_required", "qr_url": prepared.url, "expires_at": prepared.expires_at.isoformat()}

    async def qr_status(self, user_id: int, flow_id: UUID) -> dict[str, object]:
        """Return runtime state; QRLogin.wait owns the actual authorization."""
        flow = self._load_flow(user_id, flow_id, allow_completed=True)
        if flow.status == "completed":
            return {"flow_id": str(flow_id), "status": "connected"}
        if flow.status == "password_required":
            return {"flow_id": str(flow_id), "status": "password_required"}
        if flow.status != "qr_code_required":
            raise TelegramUserAuthError("This flow is not an active QR authorization", code="qr_flow_invalid")
        runtime = telegram_qr_runtime.current(user_id, flow_id)
        if runtime is None:
            self._expire_flow(flow_id)
            return {"flow_id": str(flow_id), "status": "expired"}
        return {
            "flow_id": str(flow_id),
            "status": "pending",
            "qr_url": runtime.url,
            "expires_at": runtime.qr_expires_at.isoformat(),
        }

    async def submit_code(self, user_id: int, flow_id: UUID, code: str) -> dict[str, str]:
        flow = self._load_flow(user_id, flow_id)
        client = self._api.client(self._crypto.decrypt(flow.session_ciphertext or ""))
        phone_code_hash = self._crypto.decrypt(flow.phone_code_hash_ciphertext or "")
        try:
            await client.connect()
            try:
                await client.sign_in(phone=flow.phone, code=code, phone_code_hash=phone_code_hash)
            except Exception as error:
                if not isinstance(error, SessionPasswordNeededError):
                    raise
                flow_status = "password_required"
                session = client.session.save()
                self._update_flow(flow_id, flow_status, self._crypto.encrypt(session))
                return {"flow_id": str(flow_id), "status": flow_status}
            return await self._finish(user_id, flow_id, client)
        except TelegramUserAuthError:
            raise
        except TelegramUserApiError as error:
            self._increment_attempt(flow_id)
            raise TelegramUserAuthError(str(error), code=error.code, retry_after=error.retry_after) from error
        except Exception as error:
            self._increment_attempt(flow_id)
            translated = translate_telegram_error(error, "checking the login code")
            if translated.code == "phone_code_expired":
                self._expire_flow(flow_id)
            raise TelegramUserAuthError(str(translated), code=translated.code, retry_after=translated.retry_after) from error
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
        except TelegramUserApiError as error:
            self._increment_attempt(flow_id)
            raise TelegramUserAuthError(str(error), code=error.code, retry_after=error.retry_after) from error
        except Exception as error:
            self._increment_attempt(flow_id)
            translated = translate_telegram_error(error, "checking the two-factor password")
            raise TelegramUserAuthError(str(translated), code=translated.code, retry_after=translated.retry_after) from error
        finally:
            await client.disconnect()

    async def cancel(self, user_id: int, flow_id: UUID) -> bool:
        await telegram_qr_runtime.cancel(user_id, flow_id)
        with SessionLocal() as db:
            flow = db.scalar(select(TelegramAuthFlow).where(TelegramAuthFlow.id == flow_id, TelegramAuthFlow.user_id == user_id))
            if flow is None or flow.status in {"cancelled", "completed"}:
                return False
            flow.status = "cancelled"
            flow.session_ciphertext = None
            flow.phone_code_hash_ciphertext = None
            flow.qr_expires_at = None
            db.commit()
            return True

    def _load_flow(self, user_id: int, flow_id: UUID, *, allow_completed: bool = False) -> TelegramAuthFlow:
        with SessionLocal() as db:
            flow = db.scalar(select(TelegramAuthFlow).where(TelegramAuthFlow.id == flow_id, TelegramAuthFlow.user_id == user_id))
            if flow is None or flow.status in {"cancelled", "expired"}:
                raise TelegramUserAuthError("Telegram authorization flow is invalid or expired")
            if flow.status == "completed" and not allow_completed:
                raise TelegramUserAuthError("Telegram authorization flow is invalid or expired")
            if flow.status != "completed" and flow.expires_at <= datetime.now(timezone.utc):
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

    def _expire_flow(self, flow_id: UUID) -> None:
        with SessionLocal() as db:
            flow = db.get(TelegramAuthFlow, flow_id)
            if flow is not None:
                flow.status = "expired"
                flow.session_ciphertext = None
                flow.phone_code_hash_ciphertext = None
                flow.qr_expires_at = None
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
            flow = db.get(TelegramAuthFlow, flow_id)
            account = None
            if flow is not None and flow.account_id is not None:
                account = db.scalar(select(TelegramAccount).where(TelegramAccount.id == flow.account_id, TelegramAccount.user_id == user_id).with_for_update())
            elif flow is not None and flow.account_label:
                account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id, TelegramAccount.label == flow.account_label).with_for_update())
            else:
                accounts = list(db.scalars(select(TelegramAccount).where(TelegramAccount.user_id == user_id).with_for_update()))
                if len(accounts) > 1:
                    raise TelegramUserAuthError("Multiple Telegram accounts are connected; choose account_id")
                account = accounts[0] if accounts else None
            if account is None:
                label = (flow.account_label if flow is not None else None) or "legacy"
                account = TelegramAccount(user_id=user_id, label=label)
                db.add(account)
            account.telegram_user_id = telegram_user_id
            account.username = getattr(me, "username", None)
            account.display_name = display_name
            account.session_ciphertext = session_ciphertext
            account.status = "active"
            account.last_connected_at = now
            account.last_error = None
            if flow is not None:
                if account.id is None:
                    db.flush()
                flow.account_id = account.id
                flow.status = "completed"
                flow.session_ciphertext = None
                flow.phone_code_hash_ciphertext = None
                flow.qr_expires_at = None
            db.commit()
        return {"flow_id": str(flow_id), "status": "connected", "account_id": account.id, "account_label": account.label, "telegram_user_id": str(telegram_user_id), "display_name": display_name}

    def status(self, user_id: int, account_id: int | None = None) -> dict[str, object]:
        with SessionLocal() as db:
            query = select(TelegramAccount).where(TelegramAccount.user_id == user_id)
            if account_id is not None:
                query = query.where(TelegramAccount.id == account_id)
            accounts = list(db.scalars(query.order_by(TelegramAccount.id)))
            if not accounts:
                return {"connected": False, "status": "not_connected"}
            if account_id is None and len(accounts) > 1:
                return {"connected": any(account.status == "active" for account in accounts), "status": "multiple", "accounts": [self._account_result(account) for account in accounts]}
            account = accounts[0]
            return {"connected": account.status == "active", "status": account.status, "account_id": account.id, "account_label": account.label or "legacy", "telegram_user_id": account.telegram_user_id, "username": account.username, "display_name": account.display_name, "last_connected_at": account.last_connected_at.isoformat() if account.last_connected_at else None, "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None, "last_error": account.last_error}

    def list_accounts(self, user_id: int) -> list[dict[str, object]]:
        with SessionLocal() as db:
            return [self._account_result(account) for account in db.scalars(select(TelegramAccount).where(TelegramAccount.user_id == user_id).order_by(TelegramAccount.id))]

    def rename(self, user_id: int, account_id: int, label: str) -> dict[str, object]:
        label = label.strip()
        if not label:
            raise TelegramUserAuthError("Account label is required")
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.id == account_id, TelegramAccount.user_id == user_id).with_for_update())
            if account is None:
                raise TelegramUserAuthError("Telegram account was not found")
            duplicate = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id, TelegramAccount.label == label, TelegramAccount.id != account_id))
            if duplicate is not None:
                raise TelegramUserAuthError("Telegram account label is already in use")
            account.label = label
            db.commit()
            return self._account_result(account)

    def disconnect(self, user_id: int, account_id: int | None = None) -> bool:
        with SessionLocal() as db:
            query = select(TelegramAccount).where(TelegramAccount.user_id == user_id)
            if account_id is not None:
                query = query.where(TelegramAccount.id == account_id)
            accounts = list(db.scalars(query.with_for_update()))
            if account_id is None and len(accounts) > 1:
                raise TelegramUserAuthError("Multiple Telegram accounts are connected; choose account_id")
            account = accounts[0] if accounts else None
            if account is None:
                return False
            account.status = "revoked"
            account.session_ciphertext = None
            db.commit()
            return True

    @staticmethod
    def _account_result(account: TelegramAccount) -> dict[str, object]:
        return {"account_id": account.id, "account_label": account.label or "legacy", "connected": account.status == "active", "status": account.status, "telegram_user_id": account.telegram_user_id, "username": account.username, "display_name": account.display_name, "last_connected_at": account.last_connected_at.isoformat() if account.last_connected_at else None, "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None, "last_error": account.last_error}

    def _validate_target(self, user_id: int, account_id: int | None, account_label: str | None) -> None:
        if account_label is not None and not account_label.strip():
            raise TelegramUserAuthError("Account label is required")
        if account_id is None and account_label is None:
            return
        with SessionLocal() as db:
            if account_id is not None and db.scalar(select(TelegramAccount.id).where(TelegramAccount.id == account_id, TelegramAccount.user_id == user_id)) is None:
                raise TelegramUserAuthError("Telegram account was not found")
            if account_id is None and account_label is not None and db.scalar(select(TelegramAccount.id).where(TelegramAccount.user_id == user_id, TelegramAccount.label == account_label.strip())) is not None:
                raise TelegramUserAuthError("Telegram account label is already in use")

    @staticmethod
    def _flow_scope_filters(account_id: int | None, account_label: str | None):
        if account_id is not None:
            return (TelegramAuthFlow.account_id == account_id,)
        if account_label is not None:
            return (TelegramAuthFlow.account_label == account_label.strip(),)
        return ()

    def _update_qr_metadata(self, flow_id: UUID, expires_at) -> None:
        with SessionLocal() as db:
            flow = db.get(TelegramAuthFlow, flow_id)
            if flow is not None and flow.status == "qr_code_required":
                flow.qr_expires_at = expires_at
                db.commit()
