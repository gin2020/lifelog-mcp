"""Unit tests for Telegram contact search and allowlist resolution."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramPeer, TelegramUserApi, TelegramUserApiError
from app.services.telegram_user_service import TelegramUserService, TelegramUserServiceError


class ContactSearchClient:
    def __init__(self):
        self.dasha = SimpleNamespace(
            id=123456789,
            access_hash=987,
            username=None,
            first_name="Даша",
            last_name="Иванова",
            phone="79493874752",
            contact=True,
            broadcast=False,
            megagroup=False,
        )
        self.dasha_other = SimpleNamespace(
            id=223456789,
            access_hash=988,
            username=None,
            first_name="Даша",
            last_name="Петрова",
            phone="79990000000",
            contact=True,
            broadcast=False,
            megagroup=False,
        )
        self.sergey = SimpleNamespace(
            id=323456789,
            access_hash=989,
            username="sergey",
            first_name="Сергей",
            last_name="Иванов",
            phone=None,
            contact=False,
            broadcast=False,
            megagroup=False,
        )

    async def __call__(self, request):
        return SimpleNamespace(users=[self.dasha, self.dasha_other])

    async def get_entity(self, reference):
        if reference in {"@sergey", "sergey"}:
            return self.sergey
        if reference == 123456789:
            raise ValueError("ID is not cached")
        raise ValueError("not found")


class TelegramContactSearchTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            telegram_api_id=123,
            telegram_api_hash=SecretStr("api-hash"),
            telegram_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )
        self.api = TelegramUserApi(self.settings)
        self.client = ContactSearchClient()

    def test_search_by_phone_finds_contact_without_username(self):
        result = asyncio.run(self.api.search_contacts(self.client, "+79493874752"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].telegram_peer_id, 123456789)
        self.assertIsNone(result[0].username)
        self.assertEqual(result[0].phone, "+79493874752")
        self.assertTrue(result[0].is_contact)

    def test_search_by_username_with_and_without_at_sign(self):
        with_at = asyncio.run(self.api.search_contacts(self.client, "@sergey"))
        without_at = asyncio.run(self.api.search_contacts(self.client, "sergey"))
        self.assertEqual(with_at[0].telegram_peer_id, 323456789)
        self.assertEqual(without_at[0].telegram_peer_id, 323456789)

    def test_search_by_exact_id_falls_back_to_contacts(self):
        result = asyncio.run(self.api.search_contacts(self.client, "123456789"))
        self.assertEqual([peer.telegram_peer_id for peer in result], [123456789])

    def test_search_by_phone_without_plus_is_supported_when_not_an_id(self):
        result = asyncio.run(self.api.search_contacts(self.client, "79493874752"))
        self.assertEqual([peer.telegram_peer_id for peer in result], [123456789])

    def test_search_by_name_returns_multiple_results(self):
        result = asyncio.run(self.api.search_contacts(self.client, "даша"))
        self.assertEqual([peer.telegram_peer_id for peer in result], [123456789, 223456789])
        full_name = asyncio.run(self.api.search_contacts(self.client, "Даша Иванова"))
        self.assertEqual([peer.telegram_peer_id for peer in full_name], [123456789])

    def test_search_not_found_returns_empty_list(self):
        self.assertEqual(asyncio.run(self.api.search_contacts(self.client, "Несуществующий")), [])
        self.assertEqual(asyncio.run(self.api.search_contacts(self.client, "+70000000000")), [])

    def test_api_resolve_peer_reports_nonexistent_peer(self):
        with self.assertRaises(TelegramUserApiError):
            asyncio.run(self.api.resolve_peer(self.client, 999999999))


class FakeConnectedClient:
    def __init__(self, entity):
        self.entity = entity

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return True

    async def get_entity(self, reference):
        if reference == 123456789:
            return self.entity
        raise ValueError("not found")


class FakeDb:
    added = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def scalar(self, statement):
        return None

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass


class TelegramAllowlistSearchTestCase(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            database_url="postgresql+psycopg://user:pass@localhost/lifelog",
            default_user_telegram_id=1,
            telegram_api_id=123,
            telegram_api_hash=SecretStr("api-hash"),
            telegram_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )
        self.crypto = TelegramSessionCrypto(self.settings)
        self.entity = SimpleNamespace(
            id=123456789,
            access_hash=987,
            username=None,
            first_name="Даша",
            last_name="Иванова",
            phone="79493874752",
            contact=True,
            broadcast=False,
            megagroup=False,
        )

    def test_search_then_add_preserves_peer_id_without_username(self):
        client = FakeConnectedClient(self.entity)

        class FakeApi:
            def client(self, session=None):
                return client

            async def search_contacts(self, client, query):
                return [TelegramPeer("user", 123456789, 987, None, "Даша Иванова", "+79493874752", True)]

            async def resolve_peer(self, client, reference):
                self.assert_reference = reference
                return TelegramPeer("user", 123456789, 987, None, "Даша Иванова", "+79493874752", True)

        service = TelegramUserService(self.settings, api=FakeApi())
        account = SimpleNamespace(status="active", session_ciphertext=self.crypto.encrypt("session"))
        service._account = lambda user_id: account
        with patch("app.services.telegram_user_service.SessionLocal", FakeDb):
            result = asyncio.run(service.search_contacts(7, "+79493874752"))
            added = asyncio.run(service.add_allowed_peer(7, result["results"][0]["telegram_peer_id"]))
        self.assertEqual(result["results"][0]["telegram_peer_id"], 123456789)
        self.assertIsNone(result["results"][0]["username"])
        self.assertEqual(added["telegram_peer_id"], 123456789)
        self.assertIsNone(added["username"])
        self.assertEqual(FakeDb.added[-1].telegram_peer_id, 123456789)

    def test_not_connected_search_is_rejected(self):
        service = TelegramUserService(self.settings)
        with patch("app.services.telegram_user_service.SessionLocal", FakeDb):
            with self.assertRaises(TelegramUserServiceError) as context:
                asyncio.run(service.search_contacts(7, "Даша"))
        self.assertIn("not connected", str(context.exception).lower())

    def test_adding_nonexistent_peer_is_rejected(self):
        class FakeApi:
            def client(self, session=None):
                return FakeConnectedClient(None)

            async def resolve_peer(self, client, reference):
                raise TelegramUserApiError("Telegram peer could not be resolved")

        service = TelegramUserService(self.settings, api=FakeApi())
        account = SimpleNamespace(status="active", session_ciphertext=self.crypto.encrypt("session"))
        service._account = lambda user_id: account
        with self.assertRaises(TelegramUserServiceError):
            asyncio.run(service.add_allowed_peer(7, 999999999))


if __name__ == "__main__":
    unittest.main()
