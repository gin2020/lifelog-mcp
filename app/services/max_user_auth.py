"""Staged QR authorization and encrypted session persistence for MAX."""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.max_user import MaxAccount, MaxAuthFlow
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxClientHandle, MaxUserApi, MaxUserApiError


logger = logging.getLogger(__name__)

_FLOW_FAILURE_CODES = {
    "qr_unavailable",
    "qr_expired",
    "connection_error",
    "timeout",
    "session_invalid",
    "auth_error",
    "2fa_required",
    "identity_missing",
    "max_error",
}


class MaxUserAuthError(RuntimeError):
    """Safe user-facing MAX authorization error."""

    def __init__(self, message: str, *, code: str = "max_auth_error") -> None:
        super().__init__(message)
        self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {self.args[0]}"


class MaxQrHandler:
    """Capture the QR URL without printing it or blocking the auth flow."""

    def __init__(self) -> None:
        self.url: str | None = None
        self.ready = asyncio.Event()

    async def show_qr(self, qr_url: str) -> None:
        self.url = qr_url
        self.ready.set()


class MaxQrRuntime:
    def __init__(self, user_id: int, flow_id: UUID, handle: MaxClientHandle, qr: MaxQrHandler, deadline: datetime) -> None:
        self.user_id = user_id
        self.flow_id = flow_id
        self.handle = handle
        self.qr = qr
        self.deadline = deadline
        self.authorized = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.client_task: asyncio.Task | None = None


class MaxQrRuntimeManager:
    """Keep live PyMax QR clients in memory while the user scans the code."""

    def __init__(self) -> None:
        self._runtimes: dict[tuple[int, UUID], MaxQrRuntime] = {}

    def current(self, user_id: int, flow_id: UUID) -> MaxQrRuntime | None:
        return self._runtimes.get((user_id, flow_id))

    async def cancel_user(self, user_id: int, account_id: int | None = None, account_label: str | None = None) -> None:
        runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            if runtime.user_id != user_id:
                continue
            if account_id is not None and self._flow_matches(runtime.flow_id, account_id, account_label):
                await self.cancel(user_id, runtime.flow_id)
            elif account_id is None and account_label is None:
                await self.cancel(user_id, runtime.flow_id)

    @staticmethod
    def _flow_matches(flow_id: UUID, account_id: int | None, account_label: str | None) -> bool:
        # The durable flow row is authoritative for the optional account scope.
        with SessionLocal() as db:
            flow = db.get(MaxAuthFlow, flow_id)
            if flow is None:
                return False
            return (account_id is not None and flow.account_id == account_id) or (account_id is None and flow.account_label == account_label)

    async def cancel(self, user_id: int, flow_id: UUID) -> None:
        runtime = self._runtimes.pop((user_id, flow_id), None)
        if runtime is None:
            return
        if runtime.task is not None and not runtime.task.done() and runtime.task is not asyncio.current_task():
            runtime.task.cancel()
            try:
                await runtime.task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await runtime.handle.close()


max_qr_runtime = MaxQrRuntimeManager()


class MaxUserAuthService:
    """Start QR login and persist only encrypted PyMax session files."""

    def __init__(self, settings: Settings | None = None, api: MaxUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or MaxUserApi(self._settings)
        self._crypto = MaxSessionCrypto(self._settings)

    async def qr_start(self, user_id: int, account_id: int | None = None, account_label: str | None = None) -> dict[str, object]:
        self._validate_target(user_id, account_id, account_label)
        await max_qr_runtime.cancel_user(user_id)
        flow_id = uuid4()
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(seconds=self._settings.max_user_auth_flow_ttl_seconds)
        qr = MaxQrHandler()
        handle = self._api.create_client(qr_provider=qr)
        runtime = MaxQrRuntime(user_id, flow_id, handle, qr, deadline)
        try:
            with SessionLocal() as db:
                self._cancel_active_flows(db, user_id, account_id, account_label)
                db.add(MaxAuthFlow(
                    id=flow_id,
                    user_id=user_id,
                    account_id=account_id,
                    account_label=account_label.strip() if account_label else None,
                    status="qr_code_required",
                    expires_at=deadline,
                ))
                db.commit()

            @handle.client.on_start()
            async def _authorized(_client) -> None:
                runtime.authorized.set()

            max_qr_runtime._runtimes[(user_id, flow_id)] = runtime
            runtime.task = asyncio.create_task(self._run_qr(runtime, account_id, account_label), name=f"max-qr-{user_id}-{flow_id}")
            runtime.task.add_done_callback(self._consume_task_exception)
        except Exception:
            max_qr_runtime._runtimes.pop((user_id, flow_id), None)
            await handle.close()
            raise

        qr_wait = asyncio.create_task(qr.ready.wait(), name=f"max-qr-ready-{flow_id}")
        try:
            done, _ = await asyncio.wait(
                {qr_wait, runtime.task},
                timeout=min(30, self._settings.max_user_auth_flow_ttl_seconds),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runtime.task in done:
                self._raise_task_error(runtime.task)
                result = await self.qr_status(user_id, flow_id)
                if result.get("status") == "connected":
                    return result
                raise MaxUserAuthError("MAX QR authorization ended before returning a QR code", code="qr_unavailable")
            if qr_wait not in done:
                self._mark_flow_failure(flow_id, "qr_unavailable")
                raise MaxUserAuthError("MAX did not return a QR code", code="qr_unavailable")
        except MaxUserAuthError:
            await max_qr_runtime.cancel(user_id, flow_id)
            raise
        finally:
            if not qr_wait.done():
                qr_wait.cancel()
            with suppress(asyncio.CancelledError):
                await qr_wait

        return {"flow_id": str(flow_id), "status": "qr_code_required", "qr_url": qr.url, "expires_at": deadline.isoformat()}

    async def _run_qr(self, runtime: MaxQrRuntime, account_id: int | None, account_label: str | None) -> None:
        runtime.client_task = asyncio.create_task(self._api.start(runtime.handle), name=f"max-client-{runtime.flow_id}")
        authorized_wait: asyncio.Task | None = None
        try:
            authorized_wait = asyncio.create_task(runtime.authorized.wait(), name=f"max-authorized-{runtime.flow_id}")
            done, _ = await asyncio.wait(
                {authorized_wait, runtime.client_task},
                timeout=max(1, (runtime.deadline - datetime.now(timezone.utc)).total_seconds()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise MaxUserAuthError("The MAX QR code expired; start a new authorization flow", code="qr_expired")
            if runtime.client_task in done:
                self._raise_task_error(runtime.client_task)
                if not runtime.authorized.is_set():
                    raise MaxUserAuthError("MAX authorization ended before login", code="auth_error")
            await self._finish(runtime, account_id, account_label)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            safe_error = self._safe_auth_error(error)
            self._mark_flow_failure(runtime.flow_id, safe_error.code)
            logger.info(
                "MAX QR authorization failed: user_id=%s flow_id=%s error_code=%s exception_class=%s",
                runtime.user_id,
                runtime.flow_id,
                safe_error.code,
                error.__class__.__name__,
            )
            raise safe_error from error
        finally:
            if authorized_wait is not None and not authorized_wait.done():
                authorized_wait.cancel()
            if authorized_wait is not None:
                with suppress(asyncio.CancelledError):
                    await authorized_wait
            await self._stop_runtime_client(runtime)
            max_qr_runtime._runtimes.pop((runtime.user_id, runtime.flow_id), None)

    async def _stop_runtime_client(self, runtime: MaxQrRuntime) -> None:
        client_task = runtime.client_task
        if client_task is not None and not client_task.done():
            with suppress(Exception):
                await runtime.handle.close()
            try:
                await asyncio.wait_for(asyncio.shield(client_task), timeout=5)
            except asyncio.TimeoutError:
                client_task.cancel()
                with suppress(asyncio.CancelledError):
                    await client_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        elif client_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await client_task
        with suppress(Exception):
            await runtime.handle.close()

    @staticmethod
    def _safe_auth_error(error: Exception) -> MaxUserAuthError:
        if isinstance(error, MaxUserAuthError):
            return error
        if isinstance(error, MaxUserApiError):
            return MaxUserAuthError(str(error), code=error.code)
        return MaxUserAuthError("MAX authorization failed", code="auth_error")

    @classmethod
    def _raise_task_error(cls, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError as error:
            raise MaxUserAuthError("MAX authorization was cancelled", code="auth_error") from error
        except Exception as error:
            raise cls._safe_auth_error(error) from error

    @staticmethod
    def _consume_task_exception(task: asyncio.Task) -> None:
        with suppress(asyncio.CancelledError, Exception):
            task.exception()

    @staticmethod
    def _failure_message(code: str) -> str:
        return {
            "qr_unavailable": "MAX did not return a QR code",
            "qr_expired": "The MAX QR code expired; start a new authorization flow",
            "connection_error": "MAX connection failed during authorization",
            "timeout": "MAX authorization timed out",
            "session_invalid": "The MAX session is no longer authorized",
            "auth_error": "MAX authorization failed",
            "2fa_required": "MAX requires a 2FA password; headless QR authorization cannot continue",
            "identity_missing": "MAX did not return an account identity or session",
            "max_error": "MAX authorization failed",
        }.get(code, "MAX authorization failed")

    @classmethod
    def _mark_flow_failure(cls, flow_id: UUID, code: str) -> None:
        status = code if code in _FLOW_FAILURE_CODES else "max_error"
        with SessionLocal() as db:
            flow = db.get(MaxAuthFlow, flow_id)
            if flow is not None and flow.status == "qr_code_required":
                flow.status = status
                db.commit()

    async def _finish(self, runtime: MaxQrRuntime, account_id: int | None, account_label: str | None) -> dict[str, object]:
        profile = self._api.profile_from_client(runtime.handle.client)
        max_user_id = profile.get("max_user_id")
        session_blob = runtime.handle.read_session()
        if not isinstance(max_user_id, int) or not session_blob:
            raise MaxUserAuthError("MAX did not return an account identity or session", code="identity_missing")
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            flow = db.get(MaxAuthFlow, runtime.flow_id)
            account = None
            if account_id is not None:
                account = db.scalar(select(MaxAccount).where(MaxAccount.id == account_id, MaxAccount.user_id == runtime.user_id).with_for_update())
            elif account_label:
                account = db.scalar(select(MaxAccount).where(MaxAccount.user_id == runtime.user_id, MaxAccount.label == account_label.strip()).with_for_update())
            else:
                accounts = list(db.scalars(select(MaxAccount).where(MaxAccount.user_id == runtime.user_id).with_for_update()))
                if len(accounts) > 1:
                    raise MaxUserAuthError("Multiple MAX accounts are connected; choose account_id")
                account = accounts[0] if accounts else None
            if account is None:
                account = MaxAccount(user_id=runtime.user_id, label=(account_label or "legacy").strip())
                db.add(account)
                db.flush()
            account.max_user_id = max_user_id
            account.device_id = getattr(getattr(runtime.handle.client, "extra_config", None), "device_id", None)
            account.phone = str(profile.get("phone")) if profile.get("phone") is not None else None
            account.display_name = profile.get("display_name")
            account.session_ciphertext = self._crypto.encrypt(session_blob)
            account.status = "active"
            account.last_connected_at = now
            account.last_sync_at = now
            account.last_error = None
            if flow is not None:
                flow.account_id = account.id
                flow.status = "completed"
            db.commit()
            return {"flow_id": str(runtime.flow_id), "status": "connected", "account_id": account.id, "account_label": account.label, "max_user_id": str(max_user_id), "display_name": account.display_name}

    async def qr_status(self, user_id: int, flow_id: UUID) -> dict[str, object]:
        with SessionLocal() as db:
            flow = db.scalar(select(MaxAuthFlow).where(MaxAuthFlow.id == flow_id, MaxAuthFlow.user_id == user_id))
            if flow is None:
                raise MaxUserAuthError("MAX authorization flow was not found")
            if flow.status == "completed":
                return {"flow_id": str(flow_id), "status": "connected"}
            if flow.status == "cancelled":
                return {"flow_id": str(flow_id), "status": "cancelled"}
            if flow.status in _FLOW_FAILURE_CODES:
                if flow.status == "qr_expired":
                    return {"flow_id": str(flow_id), "status": "expired", "error_code": flow.status, "error": self._failure_message(flow.status)}
                return {"flow_id": str(flow_id), "status": "failed", "error_code": flow.status, "error": self._failure_message(flow.status)}
            expires_at = flow.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                flow.status = "qr_expired"
                db.commit()
                return {"flow_id": str(flow_id), "status": "expired", "error_code": "qr_expired", "error": self._failure_message("qr_expired")}
            runtime = max_qr_runtime.current(user_id, flow_id)
            if runtime is None:
                flow.status = "qr_expired"
                db.commit()
                return {"flow_id": str(flow_id), "status": "expired", "error_code": "qr_expired", "error": self._failure_message("qr_expired")}
            return {"flow_id": str(flow_id), "status": "pending", "qr_url": runtime.qr.url, "expires_at": flow.expires_at.isoformat()}

    async def cancel(self, user_id: int, flow_id: UUID) -> bool:
        await max_qr_runtime.cancel(user_id, flow_id)
        with SessionLocal() as db:
            flow = db.scalar(select(MaxAuthFlow).where(MaxAuthFlow.id == flow_id, MaxAuthFlow.user_id == user_id))
            if flow is None or flow.status in {"cancelled", "completed", *_FLOW_FAILURE_CODES}:
                return False
            flow.status = "cancelled"
            db.commit()
            return True

    def status(self, user_id: int, account_id: int | None = None) -> dict[str, object]:
        with SessionLocal() as db:
            query = select(MaxAccount).where(MaxAccount.user_id == user_id)
            if account_id is not None:
                query = query.where(MaxAccount.id == account_id)
            accounts = list(db.scalars(query.order_by(MaxAccount.id)))
            if not accounts:
                return {"connected": False, "status": "not_connected"}
            if account_id is None and len(accounts) > 1:
                return {"connected": any(account.status == "active" for account in accounts), "status": "multiple", "accounts": [self._account_result(account) for account in accounts]}
            return self._account_result(accounts[0])

    def list_accounts(self, user_id: int) -> list[dict[str, object]]:
        with SessionLocal() as db:
            return [self._account_result(account) for account in db.scalars(select(MaxAccount).where(MaxAccount.user_id == user_id).order_by(MaxAccount.id))]

    def disconnect(self, user_id: int, account_id: int | None = None) -> bool:
        with SessionLocal() as db:
            query = select(MaxAccount).where(MaxAccount.user_id == user_id)
            if account_id is not None:
                query = query.where(MaxAccount.id == account_id)
            accounts = list(db.scalars(query.with_for_update()))
            if account_id is None and len(accounts) > 1:
                raise MaxUserAuthError("Multiple MAX accounts are connected; choose account_id")
            if not accounts:
                return False
            account = accounts[0]
            account.status = "revoked"
            account.session_ciphertext = None
            db.commit()
            return True

    def _validate_target(self, user_id: int, account_id: int | None, account_label: str | None) -> None:
        if account_label is not None and not account_label.strip():
            raise MaxUserAuthError("Account label is required")
        with SessionLocal() as db:
            if account_id is not None and db.scalar(select(MaxAccount.id).where(MaxAccount.id == account_id, MaxAccount.user_id == user_id)) is None:
                raise MaxUserAuthError("MAX account was not found")
            if account_id is None and account_label is not None and db.scalar(select(MaxAccount.id).where(MaxAccount.user_id == user_id, MaxAccount.label == account_label.strip())) is not None:
                raise MaxUserAuthError("MAX account label is already in use")

    @staticmethod
    def _cancel_active_flows(db, user_id: int, account_id: int | None, account_label: str | None) -> None:
        query = select(MaxAuthFlow).where(MaxAuthFlow.user_id == user_id, MaxAuthFlow.expires_at > datetime.now(timezone.utc))
        if account_id is not None:
            query = query.where(MaxAuthFlow.account_id == account_id)
        elif account_label is not None:
            query = query.where(MaxAuthFlow.account_label == account_label.strip())
        for flow in db.scalars(query):
            flow.status = "cancelled"

    def _expire_flow(self, flow_id: UUID) -> None:
        with SessionLocal() as db:
            flow = db.get(MaxAuthFlow, flow_id)
            if flow is not None and flow.status == "qr_code_required":
                flow.status = "expired"
                db.commit()

    @staticmethod
    def _account_result(account: MaxAccount) -> dict[str, object]:
        return {"account_id": account.id, "account_label": account.label, "connected": account.status == "active", "status": account.status, "max_user_id": account.max_user_id, "display_name": account.display_name, "phone": account.phone, "device_id": account.device_id, "last_connected_at": account.last_connected_at.isoformat() if account.last_connected_at else None, "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None, "last_error": account.last_error}
