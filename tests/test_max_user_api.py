"""Unit tests for MAX user-client primitives without network access."""

import asyncio
from types import SimpleNamespace
import unittest

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxUserApi
from app.services.max_user_auth import MaxQrHandler


class MaxUserApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            max_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )

    def test_session_blob_round_trip(self) -> None:
        crypto = MaxSessionCrypto(self.settings)
        encrypted = crypto.encrypt(b"sqlite-session-bytes")
        self.assertNotEqual(encrypted, "sqlite-session-bytes")
        self.assertEqual(crypto.decrypt(encrypted), b"sqlite-session-bytes")

    def test_profile_chat_and_message_are_normalized(self) -> None:
        profile = SimpleNamespace(contact=SimpleNamespace(id=42, phone="79990000000", names=[{"name": "Иван"}]))
        client = SimpleNamespace(me=profile)
        normalized_profile = MaxUserApi.profile_from_client(client)
        self.assertEqual(normalized_profile["max_user_id"], 42)
        self.assertEqual(normalized_profile["display_name"], "Иван")

        chat = MaxUserApi.chat_from_object(SimpleNamespace(id=100, title="Личный чат", type="DIALOG"))
        self.assertEqual(chat.max_chat_id, 100)
        self.assertEqual(chat.display_name, "Личный чат")

        message = MaxUserApi.message_from_object(SimpleNamespace(id="7", chat_id=100, text="hello", sender=42, out=False))
        self.assertEqual(message["id"], 7)
        self.assertEqual(message["chat_id"], 100)
        self.assertEqual(message["sender_id"], 42)
        self.assertFalse(message["out"])

    def test_async_history_and_send_adapter(self) -> None:
        class FakeClient:
            async def fetch_chats(self):
                return [SimpleNamespace(id=100, title="Test")]

            async def fetch_history(self, **kwargs):
                self.history_kwargs = kwargs
                return [SimpleNamespace(id=8, chat_id=100, text="incoming", out=False)]

            async def send_message(self, **kwargs):
                self.send_kwargs = kwargs
                return SimpleNamespace(id=9, chat_id=100, text=kwargs["text"], out=True)

        client = FakeClient()
        chats = asyncio.run(MaxUserApi.get_chats(client))
        history = asyncio.run(MaxUserApi.get_history(client, 100, 10))
        message_id = asyncio.run(MaxUserApi.send_message(client, 100, "outgoing"))
        self.assertEqual(chats[0].max_chat_id, 100)
        self.assertEqual(history[0]["id"], 8)
        self.assertEqual(message_id, 9)
        self.assertEqual(client.send_kwargs["chat_id"], 100)

    def test_qr_handler_does_not_log_or_print_url(self) -> None:
        handler = MaxQrHandler()
        asyncio.run(handler.show_qr("max://qr/secret"))
        self.assertEqual(handler.url, "max://qr/secret")
        self.assertTrue(handler.ready.is_set())
