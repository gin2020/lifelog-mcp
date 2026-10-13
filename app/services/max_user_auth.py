"""Staged QR authorization and encrypted session persistence for MAX."""

import asyncio
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

        runtime.task = asyncio.create_task(self._run_qr(runtime, account_id, account_label), name=f"max-qr-{user_id}-{flow_id}")
        max_qr_runtime._runtimes[(user_id, flow_id)] = runtime
        try:
            await asyncio.wait_for(qr.ready.wait(), timeout=min(30, self._settings.max_user_auth_flow_ttl_seconds))
        except asyncio.TimeoutError as error:
            await max_qr_runtime.cancel(user_id, flow_id)
            raise MaxUserAuthError("MAX did not return a QR code", code="qr_unavailable") from error
        if runtime.task.done() and runtime.task.exception() is not None:
            error = runtime.task.exception()
            await max_qr_runtime.cancel(user_id, flow_id)
            raise MaxUserAuthError(str(error), code=getattr(error, "code", "max_auth_error")) from error

        return {"flow_id": str(flow_id), "status": "qr_code_required", "qr_url": qr.url, "expires_at": deadline.isoformat()}

    async def _run_qr(self, runtime: MaxQrRuntime, account_id: int | None, account_label: str | None) -> None:
        try:
            await asyncio.wait_for(runtime.authorized.wait(), timeout=max(1, (runtime.deadline - datetime.now(timezone.utc)).total_seconds()))
            await self._finish(runtime, account_id, account_label)
        except asyncio.CancelledError:
            raise
        except MaxUserAuthError:
            self._expire_flow(runtime.flow_id)
            raise
        except Exception as error:
            self._expire_flow(runtime.flow_id)
            logger.info("MAX QR authorization failed: user_id=%s flow_id=%s error=%s", runtime.user_id, runtime.flow_id, error.__class__.__name__)
        finally:
            try:
                await runtime.handle.close()
            finally:
                max_qr_runtime._runtimes.pop((runtime.user_id, runtime.flow_id), None)

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
            if flow.status in {"cancelled", "expired"} or flow.expires_at <= datetime.now(timezone.utc):
                return {"flow_id": str(flow_id), "status": "expired"}
            runtime = max_qr_runtime.current(user_id, flow_id)
            if runtime is None:
                flow.status = "expired"
                db.commit()
                return {"flow_id": str(flow_id), "status": "expired"}
            return {"flow_id": str(flow_id), "status": "pending", "qr_url": runtime.qr.url, "expires_at": flow.expires_at.isoformat()}

    async def cancel(self, user_id: int, flow_id: UUID) -> bool:
        await max_qr_runtime.cancel(user_id, flow_id)
        with SessionLocal() as db:
            flow = db.scalar(select(MaxAuthFlow).where(MaxAuthFlow.id == flow_id, MaxAuthFlow.user_id == user_id))
            if flow is None or flow.status in {"cancelled", "completed"}:
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
