"""Unit tests for Telegram User API primitives without network access."""

import asyncio
import unittest
from types import SimpleNamespace

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApi


class TelegramUserApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            telegram_api_id=123,
            telegram_api_hash=SecretStr("api-hash"),
            telegram_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )

    def test_session_ciphertext_round_trip(self) -> None:
        crypto = TelegramSessionCrypto(self.settings)
        encrypted = crypto.encrypt("mtproto-session")
        self.assertNotEqual(encrypted, "mtproto-session")
        self.assertEqual(crypto.decrypt(encrypted), "mtproto-session")

    def test_peer_identity_uses_numeric_id(self) -> None:
        entity = SimpleNamespace(
            id=42,
            access_hash=99,
            username="sergey",
            first_name="Sergey",
            last_name="Ivanov",
            broadcast=False,
            megagroup=False,
        )
        peer = TelegramUserApi(self.settings).peer_from_entity(entity)
        self.assertEqual(peer.peer_type, "user")
        self.assertEqual(peer.telegram_peer_id, 42)
        self.assertEqual(peer.display_name, "Sergey Ivanov")

    def test_message_adapter_returns_safe_normalized_shape(self) -> None:
        class FakeClient:
            async def get_messages(self, peer, limit, min_id):
                return [SimpleNamespace(id=7, date=None, message="hello", sender_id=42, out=False)]

        messages = asyncio.run(TelegramUserApi.get_messages(FakeClient(), 42, 10))
        self.assertEqual(messages[0]["id"], 7)
        self.assertFalse(messages[0]["out"])
        self.assertEqual(messages[0]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
