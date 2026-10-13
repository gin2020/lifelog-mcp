"""Controlled MAX QR authorization lifecycle tests without network access."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from app.config.settings import Settings
from app.db.database import SessionLocal
from app.db.models.max_user import MaxAccount, MaxAuthFlow
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxUserApiError
from app.services.max_user_auth import MaxUserAuthService, max_qr_runtime


def settings(ttl: int = 600) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://user:pass@localhost/lifelog",
        default_user_telegram_id=1,
        max_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        max_user_auth_flow_ttl_seconds=ttl,
    )


class FakeHandle:
    def __init__(self, client: "FakeClient") -> None:
        self.client = client
        self.closed = asyncio.Event()
        self.close_count = 0

    def read_session(self) -> bytes:
        return b"fake-sqlite-session"

    async def close(self) -> None:
        if self.closed.is_set():
            return
        self.close_count += 1
        self.closed.set()


class FakeClient:
    def __init__(self) -> None:
        self.on_start_handler = None
        self.me = SimpleNamespace(
            contact=SimpleNamespace(id=42, phone=79990000000, names=[{"name": "Test MAX"}]),
        )

    def on_start(self):
        def decorator(handler):
            self.on_start_handler = handler
            return handler

        return decorator


class FakeApi:
    def __init__(self, *, failure: MaxUserApiError | None = None, failure_after_qr: bool = False) -> None:
        self.failure = failure
        self.failure_after_qr = failure_after_qr
        self.release = asyncio.Event()
        self.handles: list[FakeHandle] = []
        self.start_calls = 0

    def create_client(self, session_blob=None, *, device_id=None, qr_provider=None):
        client = FakeClient()
        handle = FakeHandle(client)
        handle.qr_provider = qr_provider
        self.handles.append(handle)
        return handle

    async def start(self, handle: FakeHandle) -> None:
        self.start_calls += 1
        if self.failure is not None and not self.failure_after_qr:
            raise self.failure
        await handle.qr_provider.show_qr("max://qr/test-token")
        if self.failure is not None:
            raise self.failure
        release_wait = asyncio.create_task(self.release.wait())
        close_wait = asyncio.create_task(handle.closed.wait())
        done, pending = await asyncio.wait({release_wait, close_wait}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if close_wait in done:
            return
        await handle.client.on_start_handler(handle.client)
        await handle.closed.wait()

    @staticmethod
    def profile_from_client(client):
        return {"max_user_id": client.me.contact.id, "phone": client.me.contact.phone, "display_name": "Test MAX"}


async def cancel_runtime(flow_id: str) -> None:
    await max_qr_runtime.cancel(1, UUID(flow_id))


def test_qr_start_starts_client_and_returns_qr_while_runtime_is_live():
    async def scenario():
        api = FakeApi()
        service = MaxUserAuthService(settings(), api=api)
        result = await service.qr_start(1)
        flow_id = result["flow_id"]

        assert api.start_calls == 1
        assert result["qr_url"] == "max://qr/test-token"
        pending = await service.qr_status(1, UUID(flow_id))
        assert pending["status"] == "pending"
        assert pending["qr_url"] == result["qr_url"]
        await cancel_runtime(flow_id)
        assert api.handles[0].close_count == 1

    asyncio.run(scenario())


def test_client_error_before_qr_is_not_reported_as_qr_unavailable():
    async def scenario():
        api = FakeApi(failure=MaxUserApiError("MAX connection failed", code="connection_error"))
        service = MaxUserAuthService(settings(), api=api)
        try:
            await service.qr_start(1)
        except Exception as error:
            assert getattr(error, "code", None) == "connection_error"
            assert "qr_unavailable" not in str(error)
        else:
            raise AssertionError("qr_start must fail")
        assert api.handles[0].close_count == 1
        with SessionLocal() as db:
            flow = db.scalar(select(MaxAuthFlow).order_by(MaxAuthFlow.created_at.desc()))
            assert flow.status == "connection_error"

    asyncio.run(scenario())


def test_success_persists_encrypted_session_and_closes_once():
    async def scenario():
        api = FakeApi()
        service_settings = settings()
        service = MaxUserAuthService(service_settings, api=api)
        result = await service.qr_start(1, account_label="personal")
        api.release.set()
        for _ in range(20):
            status = await service.qr_status(1, UUID(result["flow_id"]))
            if status["status"] == "connected":
                break
            await asyncio.sleep(0.01)
        assert status["status"] == "connected"
        assert api.handles[0].close_count == 1
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.label == "personal"))
            assert account is not None
            assert account.session_ciphertext is not None
            assert MaxSessionCrypto(service_settings).decrypt(account.session_ciphertext) == b"fake-sqlite-session"

    asyncio.run(scenario())


def test_two_factor_requirement_finishes_flow_without_console_hang():
    async def scenario():
        api = FakeApi(
            failure=MaxUserApiError("MAX requires a password", code="2fa_required"),
            failure_after_qr=True,
        )
        service = MaxUserAuthService(settings(), api=api)
        result = await service.qr_start(1)
        for _ in range(20):
            status = await service.qr_status(1, UUID(result["flow_id"]))
            if status["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        assert status["error_code"] == "2fa_required"
        await asyncio.sleep(0.05)
        assert api.handles[0].close_count == 1

    asyncio.run(scenario())


def test_expiration_and_repeated_start_cleanup_old_runtime():
    async def scenario():
        api = FakeApi()
        service = MaxUserAuthService(settings(ttl=1), api=api)
        first = await service.qr_start(1)
        second = await service.qr_start(1)
        assert api.handles[0].close_count == 1
        first_status = await service.qr_status(1, UUID(first["flow_id"]))
        assert first_status["status"] == "cancelled"
        await asyncio.sleep(1.1)
        second_status = await service.qr_status(1, UUID(second["flow_id"]))
        assert second_status["status"] == "expired"
        await asyncio.sleep(0.05)
        assert api.handles[1].close_count == 1

    asyncio.run(scenario())
