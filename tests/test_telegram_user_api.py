"""Unit tests for Telegram User API primitives without network access."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApi
from app.services.telegram_user_api import translate_telegram_error
from app.services.telegram_user_auth import TelegramUserAuthService, TelegramUserAuthError
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError


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

    def test_sent_code_app_metadata_is_safe_and_explicit(self) -> None:
        # Use named classes to mirror Telethon TL constructor names.
        SentCodeTypeApp = type("SentCodeTypeApp", (), {})
        CodeTypeSms = type("CodeTypeSms", (), {})
        sent = SimpleNamespace(type=SentCodeTypeApp(), next_type=CodeTypeSms(), timeout=120, phone_code_hash="secret-hash")
        metadata = TelegramUserApi.sent_code_metadata(sent)
        self.assertEqual(metadata["delivery"], "telegram_app")
        self.assertEqual(metadata["telegram_sent_code_type"], "SentCodeTypeApp")
        self.assertEqual(metadata["telegram_next_type"], "CodeTypeSms")
        self.assertEqual(metadata["telegram_timeout"], 120)
        self.assertTrue(metadata["phone_code_hash_present"])
        self.assertNotIn("phone_code_hash", metadata)
        self.assertNotIn("secret-hash", str(metadata))

    def test_sent_code_sms_and_unknown_metadata(self) -> None:
        SentCodeTypeSms = type("SentCodeTypeSms", (), {})
        sent = SimpleNamespace(type=SentCodeTypeSms(), next_type=None, timeout=None, phone_code_hash=None)
        metadata = TelegramUserApi.sent_code_metadata(sent)
        self.assertEqual(metadata["delivery"], "sms")
        self.assertEqual(metadata["telegram_sent_code_type"], "SentCodeTypeSms")
        self.assertIsNone(metadata["telegram_next_type"])
        self.assertIsNone(metadata["telegram_timeout"])
        self.assertFalse(metadata["phone_code_hash_present"])

    def test_start_persists_phone_code_hash_and_reports_delivery(self) -> None:
        class FakeSession:
            def save(self):
                return "intermediate-session"

        class FakeClient:
            session = FakeSession()

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def send_code_request(self, phone):
                self.phone = phone
                return SimpleNamespace(phone_code_hash="hash-from-telegram", type=SimpleNamespace())

        class FakeQuery:
            def filter(self, *args):
                return self

            def update(self, values):
                return 0

        class FakeDb:
            added = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def query(self, model):
                return FakeQuery()

            def add(self, value):
                self.added.append(value)

            def commit(self):
                pass

        class FakeApi:
            def client(self, session=None):
                return FakeClient()

            sent_code_metadata = staticmethod(TelegramUserApi.sent_code_metadata)

        service = TelegramUserAuthService(self.settings, api=FakeApi())
        with patch("app.services.telegram_user_auth.SessionLocal", FakeDb), patch(
            "app.services.telegram_user_auth.logger.info"
        ) as log_info:
            result = asyncio.run(service.start(7, "+15551234567"))
        self.assertEqual(result["status"], "code_required")
        self.assertEqual(len(FakeDb.added), 1)
        flow = FakeDb.added[0]
        self.assertNotEqual(flow.phone_code_hash_ciphertext, "hash-from-telegram")
        self.assertEqual(service._crypto.decrypt(flow.phone_code_hash_ciphertext), "hash-from-telegram")
        logged = repr(log_info.call_args_list)
        self.assertNotIn("+15551234567", logged)
        self.assertNotIn("hash-from-telegram", logged)

    def test_submit_code_reuses_persisted_phone_code_hash(self) -> None:
        class FakeSession:
            def save(self):
                return "updated-session"

        class FakeClient:
            session = FakeSession()
            received = None

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def sign_in(self, **kwargs):
                self.received = kwargs

        class FakeApi:
            client_instance = FakeClient()

            def client(self, session=None):
                return self.client_instance

        service = TelegramUserAuthService(self.settings, api=FakeApi())
        flow = SimpleNamespace(
            session_ciphertext=service._crypto.encrypt("intermediate-session"),
            phone_code_hash_ciphertext=service._crypto.encrypt("hash-from-telegram"),
            phone="+15551234567",
            status="code_required",
        )
        with patch.object(service, "_load_flow", return_value=flow), patch.object(
            service, "_finish", return_value={"status": "connected"}
        ) as finish:
            result = asyncio.run(service.submit_code(7, uuid4(), "12345"))
        self.assertEqual(result["status"], "connected")
        self.assertEqual(FakeApi.client_instance.received["phone_code_hash"], "hash-from-telegram")
        finish.assert_called_once()

    def test_session_password_needed_preserves_intermediate_state(self) -> None:
        class FakeSession:
            def save(self):
                return "password-session"

        class FakeClient:
            session = FakeSession()

            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def sign_in(self, **kwargs):
                raise SessionPasswordNeededError(None)

        class FakeApi:
            def client(self, session=None):
                return FakeClient()

        service = TelegramUserAuthService(self.settings, api=FakeApi())
        flow = SimpleNamespace(
            session_ciphertext=service._crypto.encrypt("intermediate-session"),
            phone_code_hash_ciphertext=service._crypto.encrypt("hash-from-telegram"),
            phone="+15551234567",
            status="code_required",
        )
        with patch.object(service, "_load_flow", return_value=flow), patch.object(service, "_update_flow") as update:
            result = asyncio.run(service.submit_code(7, uuid4(), "12345"))
        self.assertEqual(result["status"], "password_required")
        update.assert_called_once()
        self.assertEqual(update.call_args.args[1], "password_required")

    def test_telegram_error_mapping_does_not_include_secrets(self) -> None:
        error = translate_telegram_error(PhoneCodeInvalidError(None), "checking the login code")
        self.assertEqual(error.code, "phone_code_invalid")
        self.assertIn("invalid", str(error).lower())
        self.assertNotIn("12345", str(error))

    def test_auth_error_has_machine_readable_reason(self) -> None:
        error = TelegramUserAuthError("The code is invalid", code="phone_code_invalid")
        self.assertEqual(str(error), "[phone_code_invalid] The code is invalid")


if __name__ == "__main__":
    unittest.main()
