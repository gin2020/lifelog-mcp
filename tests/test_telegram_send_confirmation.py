"""Tests for Telegram send confirmation and the MCP Apps confirmation card."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApiError
from app.services.telegram_user_service import TelegramUserService, TelegramUserServiceError


class BaseSendTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            telegram_api_id=123,
            telegram_api_hash=SecretStr("api-hash"),
            telegram_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )
        self.crypto = TelegramSessionCrypto(self.settings)
        self.peer = SimpleNamespace(
            id=12,
            peer_type="user",
            telegram_peer_id=321,
            username="NikoStarmen",
            display_name="Никита Zhuravlov",
            is_enabled=True,
        )

    def account(self):
        return SimpleNamespace(status="active", session_ciphertext=self.crypto.encrypt("session"))


class CreateDb:
    def __init__(self, peer):
        self.peer = peer
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def scalar(self, statement):
        return self.peer

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass


class ConfirmationDb:
    def __init__(self, request, peer):
        self.request = request
        self.peer = peer
        self.scalar_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def scalar(self, statement):
        self.scalar_count += 1
        return self.request if self.scalar_count == 1 else self.peer

    def get(self, model, key):
        return self.request

    def commit(self):
        pass


class FakeTelegramClient:
    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return True


class FakeApi:
    def __init__(self, message_id=77, error=None):
        self.client_instance = FakeTelegramClient()
        self.message_id = message_id
        self.error = error
        self.send_message_calls = []

    def client(self, session=None):
        return self.client_instance

    async def send_message(self, client, peer, text):
        self.send_message_calls.append((peer, text))
        if self.error:
            raise self.error
        return self.message_id


class TelegramSendConfirmationTestCase(BaseSendTestCase):
    def test_create_pending_send_returns_exact_ui_payload_without_sending(self):
        db = CreateDb(self.peer)
        api = FakeApi()
        service = TelegramUserService(self.settings, api=api)
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            result = service.create_send_request(7, 12, "Доброй ночи ❤️")
        self.assertEqual(result["status"], "confirmation_required")
        self.assertEqual(result["text"], "Доброй ночи ❤️")
        self.assertEqual(result["recipient"]["display_name"], "Никита Zhuravlov")
        self.assertEqual(result["recipient"]["username"], "NikoStarmen")
        self.assertTrue(result["request_id"])
        self.assertEqual(api.send_message_calls, [])

    def test_pending_request_does_not_send_until_confirm(self):
        db = CreateDb(self.peer)
        api = FakeApi()
        service = TelegramUserService(self.settings, api=api)
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            service.create_send_request(7, 12, "text")
        self.assertEqual(api.send_message_calls, [])

    def test_confirm_uses_request_id_and_sends_once(self):
        request = SimpleNamespace(
            id=uuid4(),
            allowed_peer_id=12,
            telegram_peer_id=321,
            message_ciphertext=self.crypto.encrypt("Доброй ночи ❤️"),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db = ConfirmationDb(request, self.peer)
        api = FakeApi(message_id=456)
        service = TelegramUserService(self.settings, api=api)
        service._account = lambda user_id: self.account()
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            result = asyncio.run(service.confirm_send(7, request.id))
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["telegram_message_id"], 456)
        self.assertEqual(api.send_message_calls, [(321, "Доброй ночи ❤️")])

    def test_cancel_invalidates_pending_request_without_sending(self):
        request = SimpleNamespace(
            id=uuid4(), status="pending", message_ciphertext=self.crypto.encrypt("text"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db = ConfirmationDb(request, self.peer)
        api = FakeApi()
        service = TelegramUserService(self.settings, api=api)
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            result = service.cancel_send(7, request.id)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(request.status, "cancelled")
        self.assertEqual(api.send_message_calls, [])

    def test_expired_request_is_rejected_and_never_sent(self):
        request = SimpleNamespace(
            id=uuid4(),
            allowed_peer_id=12,
            telegram_peer_id=321,
            message_ciphertext=self.crypto.encrypt("text"),
            status="pending",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        db = ConfirmationDb(request, self.peer)
        api = FakeApi()
        service = TelegramUserService(self.settings, api=api)
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            with self.assertRaises(TelegramUserServiceError) as context:
                asyncio.run(service.confirm_send(7, request.id))
        self.assertIn("expired", str(context.exception))
        self.assertEqual(request.status, "expired")
        self.assertEqual(api.send_message_calls, [])

    def test_already_used_request_is_rejected(self):
        request = SimpleNamespace(
            id=uuid4(), status="sent", message_ciphertext="", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db = ConfirmationDb(request, self.peer)
        service = TelegramUserService(self.settings, api=FakeApi())
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            with self.assertRaises(TelegramUserServiceError):
                asyncio.run(service.confirm_send(7, request.id))

    def test_telegram_send_error_does_not_report_success(self):
        request = SimpleNamespace(
            id=uuid4(),
            allowed_peer_id=12,
            telegram_peer_id=321,
            message_ciphertext=self.crypto.encrypt("text"),
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            error=None,
        )
        db = ConfirmationDb(request, self.peer)
        api = FakeApi(error=TelegramUserApiError("Telegram send failed"))
        service = TelegramUserService(self.settings, api=api)
        service._account = lambda user_id: self.account()
        with patch("app.services.telegram_user_service.SessionLocal", lambda: db):
            with self.assertRaises(TelegramUserServiceError):
                asyncio.run(service.confirm_send(7, request.id))
        self.assertEqual(request.status, "failed")

    def test_ui_resource_and_tool_metadata_are_registered(self):
        import app.tools.telegram_user  # noqa: F401
        from app.core.mcp import mcp

        tool = mcp._tool_manager.get_tool("telegram_send_message")
        self.assertEqual(tool.meta["ui"]["resourceUri"], "ui://telegram/send-confirmation-v1.html")
        self.assertEqual(tool.meta["openai/outputTemplate"], tool.meta["ui"]["resourceUri"])
        resource = next(item for item in mcp._resource_manager.list_resources() if str(item.uri) == tool.meta["ui"]["resourceUri"])
        self.assertEqual(resource.meta["ui"]["domain"], "https://mcp.jesarion.com")
        self.assertEqual(resource.meta["ui"]["csp"], {"connectDomains": [], "resourceDomains": []})
        html = asyncio.run(resource.read())
        self.assertIn("telegram_confirm_send", html)
        self.assertIn("telegram_cancel_send", html)
        self.assertIn("ui/notifications/tool-result", html)
        self.assertIn("Запрос на отправку истёк", html)


if __name__ == "__main__":
    unittest.main()
