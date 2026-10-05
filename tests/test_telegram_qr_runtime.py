"""Tests for the in-process Telethon QRLogin runtime owner."""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
from telethon.errors import SessionPasswordNeededError
from telethon.tl import types
from telethon.tl.custom.qrlogin import QRLogin

from app.config.settings import Settings
from app.services.telegram_qr_runtime import PreparedQrLogin, TelegramQrRuntimeManager
from app.services.telegram_session_crypto import TelegramSessionCrypto


class FakeClient:
    class Session:
        def save(self):
            return "intermediate-session"

    def __init__(self):
        self.session = self.Session()
        self.connected = True
        self.disconnect_count = 0

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False
        self.disconnect_count += 1


class FakeQrLogin:
    def __init__(self, mode="success", lifetime=60):
        self.mode = mode
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=lifetime)
        self.recreate_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def url(self):
        return f"tg://login?token=runtime-token-{self.recreate_count}"

    async def wait(self):
        self.started.set()
        if self.mode == "password":
            raise SessionPasswordNeededError(None)
        if self.mode == "success":
            return object()
        await self.release.wait()

    async def recreate(self):
        self.recreate_count += 1
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=60)


class FakeService:
    def __init__(self, settings):
        self._crypto = TelegramSessionCrypto(settings)
        self.finished = []
        self.updated = []
        self.expired = []

    async def _finish(self, user_id, flow_id, client):
        self.finished.append((user_id, flow_id))

    def _update_flow(self, flow_id, status, session):
        self.updated.append((flow_id, status, session))

    def _update_qr_metadata(self, flow_id, expires_at):
        self.updated.append((flow_id, "qr_metadata", expires_at))

    def _expire_flow(self, flow_id):
        self.expired.append(flow_id)


class TelegramQrRuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            telegram_api_id=123,
            telegram_api_hash=SecretStr("api-hash"),
            telegram_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )

    def prepared(self, client, qr):
        return PreparedQrLogin(
            client=client,
            qr_login=qr,
            url=qr.url,
            expires_at=qr.expires,
            session=client.session.save(),
        )

    def test_background_wait_completes_and_cleans_runtime(self):
        async def run():
            manager = TelegramQrRuntimeManager()
            service = FakeService(self.settings)
            client = FakeClient()
            qr = FakeQrLogin()
            flow_id = uuid4()
            await manager.register(service, 10, flow_id, self.prepared(client, qr), datetime.now(timezone.utc) + timedelta(seconds=5))
            runtime = manager.current(10, flow_id)
            self.assertIsNotNone(runtime)
            await asyncio.wait_for(runtime.task, timeout=1)
            self.assertEqual(service.finished, [(10, flow_id)])
            self.assertIsNone(manager.current(10, flow_id))
            self.assertEqual(client.disconnect_count, 1)

        asyncio.run(run())

    def test_qr_expiration_recreates_url_and_old_runtime_url_is_replaced(self):
        async def run():
            manager = TelegramQrRuntimeManager()
            service = FakeService(self.settings)
            client = FakeClient()
            qr = FakeQrLogin(mode="pending", lifetime=0.01)
            flow_id = uuid4()
            await manager.register(service, 10, flow_id, self.prepared(client, qr), datetime.now(timezone.utc) + timedelta(seconds=0.08))
            runtime = manager.current(10, flow_id)
            await asyncio.wait_for(runtime.task, timeout=1)
            self.assertGreaterEqual(qr.recreate_count, 1)
            self.assertTrue(service.expired == [flow_id])
            self.assertEqual(client.disconnect_count, 1)

        asyncio.run(run())

    def test_cancel_stops_wait_and_disconnects_client(self):
        async def run():
            manager = TelegramQrRuntimeManager()
            service = FakeService(self.settings)
            client = FakeClient()
            qr = FakeQrLogin(mode="pending")
            flow_id = uuid4()
            await manager.register(service, 10, flow_id, self.prepared(client, qr), datetime.now(timezone.utc) + timedelta(seconds=5))
            await asyncio.wait_for(qr.started.wait(), timeout=1)
            await manager.cancel(10, flow_id)
            self.assertIsNone(manager.current(10, flow_id))
            self.assertEqual(client.disconnect_count, 1)
            self.assertEqual(service.expired, [])

        asyncio.run(run())

    def test_password_required_preserves_encrypted_intermediate_session(self):
        async def run():
            manager = TelegramQrRuntimeManager()
            service = FakeService(self.settings)
            client = FakeClient()
            qr = FakeQrLogin(mode="password")
            flow_id = uuid4()
            await manager.register(service, 10, flow_id, self.prepared(client, qr), datetime.now(timezone.utc) + timedelta(seconds=5))
            runtime = manager.current(10, flow_id)
            await asyncio.wait_for(runtime.task, timeout=1)
            self.assertEqual(service.updated[0][1], "password_required")
            self.assertEqual(service._crypto.decrypt(service.updated[0][2]), "intermediate-session")
            self.assertEqual(client.disconnect_count, 1)

        asyncio.run(run())

    def test_user_and_flow_keys_are_isolated_and_logs_never_include_qr_url(self):
        async def run():
            manager = TelegramQrRuntimeManager()
            service = FakeService(self.settings)
            client_a, client_b = FakeClient(), FakeClient()
            qr_a, qr_b = FakeQrLogin(), FakeQrLogin()
            flow_a, flow_b = uuid4(), uuid4()
            with patch("app.services.telegram_qr_runtime.logger.info") as log_info:
                await manager.register(service, 10, flow_a, self.prepared(client_a, qr_a), datetime.now(timezone.utc) + timedelta(seconds=5))
                await manager.register(service, 11, flow_b, self.prepared(client_b, qr_b), datetime.now(timezone.utc) + timedelta(seconds=5))
                await asyncio.gather(manager.current(10, flow_a).task, manager.current(11, flow_b).task)
            self.assertIsNone(manager.current(10, flow_b))
            self.assertIsNone(manager.current(11, flow_a))
            self.assertNotIn("runtime-token", repr(log_info.call_args_list))

        asyncio.run(run())

    def test_telethon_qr_login_wait_handles_migrate_to_and_success(self):
        async def run():
            user = types.User(id=321, first_name="Test")
            authorization = types.auth.Authorization(user=user)

            class Client:
                api_id = 123
                api_hash = "api-hash"

                def __init__(self):
                    self.responses = [
                        types.auth.LoginTokenMigrateTo(dc_id=2, token=b"migrated-token"),
                        types.auth.LoginTokenSuccess(authorization=authorization),
                    ]
                    self.handlers = []
                    self.switched_to = None
                    self.logged_in = None

                def add_event_handler(self, handler, event):
                    self.handlers.append(handler)
                    asyncio.create_task(handler(types.UpdateLoginToken()))

                def remove_event_handler(self, handler):
                    self.handlers.remove(handler)

                async def __call__(self, request):
                    return self.responses.pop(0)

                async def _switch_dc(self, dc_id):
                    self.switched_to = dc_id

                async def _on_login(self, logged_in_user):
                    self.logged_in = logged_in_user
                    return logged_in_user

            client = Client()
            qr_login = QRLogin(client, [])
            qr_login._resp = types.auth.LoginToken(
                expires=datetime.now(timezone.utc) + timedelta(seconds=30),
                token=b"original-token",
            )
            result = await qr_login.wait()
            self.assertEqual(result.id, 321)
            self.assertEqual(client.switched_to, 2)
            self.assertEqual(client.logged_in.id, 321)

        asyncio.run(run())

    def test_telethon_qr_url_is_used_without_token_reencoding(self):
        class Client:
            api_id = 123
            api_hash = "api-hash"

        client = Client()
        qr_login = QRLogin(client, [])
        token = b"binary-qr-token"
        qr_login._resp = types.auth.LoginToken(
            expires=datetime.now(timezone.utc) + timedelta(seconds=30),
            token=token,
        )
        expected = "tg://login?token=" + base64.urlsafe_b64encode(token).decode().rstrip("=")
        self.assertEqual(qr_login.url, expected)


if __name__ == "__main__":
    unittest.main()
