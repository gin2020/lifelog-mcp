"""Unit tests for MAX user-client primitives without network access."""

import asyncio
import json
from types import SimpleNamespace
import unittest

from cryptography.fernet import Fernet
from pydantic import SecretStr
from pymax.types.domain.attachments import PhotoAttachment
from pymax.types.domain.element import Element, ElementAttributes
from pymax.types.domain.message import Message

from app.config.settings import Settings
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxNonInteractivePasswordProvider, MaxTwoFactorRequiredError, MaxUserApi, translate_max_error
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

    def test_message_json_keeps_russian_text_and_emoji(self) -> None:
        normalized = MaxUserApi.message_from_object(
            {
                "id": 8,
                "chat_id": 100,
                "text": "Привет, MAX 👋",
                "sender": 42,
                "attaches": [{"caption": "Документ 📄"}],
            }
        )

        json.dumps(normalized, ensure_ascii=False)
        self.assertEqual(normalized["text"], "Привет, MAX 👋")
        self.assertEqual(normalized["raw"]["attaches"][0]["caption"], "Документ 📄")

    def test_message_json_replaces_invalid_text_bytes_and_preserves_binary_bytes(self) -> None:
        normalized = MaxUserApi.message_from_object(
            {
                "id": 9,
                "chat_id": 100,
                "text": b"\xff\xd0\x9f",
                "stats": {"opaque": b"\xfe\x01"},
                "attaches": [{
                    "caption": b"\xd0\x94\xd0\xbe\xd0\xba\x1f",
                    "file_data": b"\xff\x00\x80",
                }],
            }
        )

        json.dumps(normalized, ensure_ascii=False)
        self.assertEqual(normalized["text"], "�П")
        attachment = normalized["raw"]["attaches"][0]
        self.assertEqual(attachment["caption"], "Док\x1f")
        self.assertNotIn("file_data", attachment)
        self.assertEqual(normalized["raw"]["stats"]["opaque"]["__lifelog_type"], "bytes")

    def test_photo_message_identifies_image_without_serializing_preview_bytes(self) -> None:
        message = Message(
            id=10,
            chat_id=100,
            sender=42,
            text="",
            time=10,
            type="MESSAGE",
            attaches=[PhotoAttachment(
                type="PHOTO",
                baseUrl="https://cdn.example/photo",
                height=480,
                width=640,
                photoId=99,
                photoToken="private-token",
                previewData=b"\xff\x00\x80",
            )],
        )
        raw = MaxUserApi.as_dict(message)
        with self.assertRaises(TypeError):
            json.dumps(raw, ensure_ascii=False)

        normalized = MaxUserApi.message_from_object(message)
        json.dumps(normalized, ensure_ascii=False)
        self.assertIsNone(normalized["text"])
        attachment = normalized["raw"]["attaches"][0]
        self.assertEqual(attachment["type"], "PHOTO")
        self.assertEqual(attachment["photo_id"], 99)
        self.assertEqual(attachment["base_url"], "https://cdn.example/photo")
        self.assertEqual(attachment["width"], 640)
        self.assertEqual(attachment["height"], 480)
        self.assertNotIn("preview_data", attachment)
        self.assertNotIn("photo_token", attachment)

    def test_message_elements_are_preserved_and_json_safe(self) -> None:
        message = Message(
            id=11,
            chat_id=100,
            sender=42,
            text="ссылка",
            time=11,
            type="MESSAGE",
            elements=[Element(
                type="LINK",
                from_=0,
                length=6,
                attributes=ElementAttributes(url="https://example.test"),
            )],
        )
        normalized = MaxUserApi.message_from_object(message)
        json.dumps(normalized, ensure_ascii=False)
        self.assertEqual(normalized["raw"]["elements"][0]["type"], "LINK")
        self.assertEqual(normalized["raw"]["elements"][0]["attributes"]["url"], "https://example.test")

    def test_history_with_text_and_photo_is_json_safe_for_larger_limits(self) -> None:
        class HistoryClient:
            async def fetch_history(self, **kwargs):
                return [
                    Message(id=20, chat_id=100, sender=42, text="Тест", time=20, type="MESSAGE"),
                    Message(
                        id=21,
                        chat_id=100,
                        sender=42,
                        text="",
                        time=21,
                        type="MESSAGE",
                        attaches=[PhotoAttachment(
                            type="PHOTO",
                            baseUrl="https://cdn.example/photo",
                            height=10,
                            width=20,
                            photoId=123,
                            photoToken="private-token",
                            previewData=b"\xff\x00",
                        )],
                    ),
                ]

        messages = asyncio.run(MaxUserApi.get_history(HistoryClient(), 100, 10))
        json.dumps(messages, ensure_ascii=False)
        self.assertEqual(messages[0]["text"], "Тест")
        self.assertEqual(messages[1]["raw"]["attaches"][0]["type"], "PHOTO")
        self.assertIsNone(messages[1]["text"])

    def test_history_passes_different_limits_to_pymax(self) -> None:
        class HistoryClient:
            def __init__(self):
                self.requests = []

            async def fetch_history(self, **kwargs):
                self.requests.append(kwargs)
                return [SimpleNamespace(id=1, chat_id=100, text="ok")]

        client = HistoryClient()
        for limit in (1, 3, 20):
            result = asyncio.run(MaxUserApi.get_history(client, 100, limit))
            self.assertEqual(result[0]["text"], "ok")
        self.assertEqual([request["backward"] for request in client.requests], [1, 3, 20])

    def test_dialogs_are_enriched_from_participants_in_one_batch(self) -> None:
        class ContactClient:
            me = SimpleNamespace(contact=SimpleNamespace(id=1))

            async def fetch_chats(self):
                return [
                    SimpleNamespace(id=100, type="DIALOG", owner=1, participants={1: 0, 42: 0}),
                    SimpleNamespace(id=200, type="CHAT", owner=1, participants={1: 0, 77: 0}, title="Рабочая группа"),
                ]

            async def get_users(self, user_ids):
                self.requested_user_ids = user_ids
                return [
                    SimpleNamespace(
                        id=42,
                        names=[SimpleNamespace(first_name="Никита", last_name="MTS")],
                        phone=79991234567,
                        username="nikita_mts",
                        link="max.ru/nikita_mts",
                    )
                ]

        client = ContactClient()
        chats = asyncio.run(MaxUserApi.get_chats(client))

        self.assertEqual(client.requested_user_ids, [1, 42, 77])
        self.assertEqual(chats[0].max_user_id, 42)
        self.assertEqual(chats[0].display_name, "Никита MTS")
        self.assertEqual(chats[0].username, "nikita_mts")
        self.assertEqual(chats[0].phone, "79991234567")
        self.assertEqual(chats[0].profile_link, "max.ru/nikita_mts")
        self.assertEqual(chats[0].participant_ids, (1, 42))
        self.assertTrue(chats[0].contact_found)
        self.assertEqual(chats[1].display_name, "Рабочая группа")
        self.assertIsNone(chats[1].max_user_id)

    def test_missing_profile_keeps_ids_and_marks_name_unavailable(self) -> None:
        class ContactClient:
            me = SimpleNamespace(contact=SimpleNamespace(id=1))

            async def fetch_chats(self):
                return [SimpleNamespace(id=100, type="DIALOG", owner=1, participants={1: 0, 42: 0})]

            async def get_users(self, user_ids):
                return []

        chat = asyncio.run(MaxUserApi.get_chats(ContactClient()))[0]
        self.assertEqual(chat.max_chat_id, 100)
        self.assertEqual(chat.max_user_id, 42)
        self.assertEqual(chat.participant_ids, (1, 42))
        self.assertIn("имя недоступно", chat.display_name)
        self.assertFalse(chat.contact_found)

    def test_qr_handler_does_not_log_or_print_url(self) -> None:
        handler = MaxQrHandler()
        asyncio.run(handler.show_qr("max://qr/secret"))
        self.assertEqual(handler.url, "max://qr/secret")
        self.assertTrue(handler.ready.is_set())

    def test_real_pymax_qr_client_uses_noninteractive_auth_flow(self) -> None:
        handler = MaxQrHandler()
        api = MaxUserApi(self.settings)
        handle = api.create_client(qr_provider=handler)
        try:
            self.assertEqual(handle.client._auth_flow.__class__.__name__, "QrAuthFlow")
            password_provider = handle.client._auth_flow.password_provider
            with self.assertRaises(MaxTwoFactorRequiredError):
                asyncio.run(password_provider.get_password("hint"))
        finally:
            asyncio.run(handle.close())

    def test_pymax_error_translation_is_safe_and_specific(self) -> None:
        translated = translate_max_error(RuntimeError("QR authentication expired"), "starting QR")
        self.assertEqual(translated.code, "qr_expired")
        self.assertNotIn("QR authentication expired", str(translated))
