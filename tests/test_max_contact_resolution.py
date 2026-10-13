"""MAX chat identity and allowlist tests without network access."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest

from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config.settings import Settings
from app.db.database import SessionLocal
from app.db.models.max_user import MaxAccount, MaxAllowedChat
from app.services.max_user_api import MaxChat
from app.services.max_user_service import MaxUserService


class MaxContactResolutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            database_url="sqlite+pysqlite:///:memory:",
            default_user_telegram_id=1,
            max_session_encryption_key=SecretStr(Fernet.generate_key().decode()),
        )

    def test_search_matches_name_username_phone_and_ids(self) -> None:
        chat = MaxChat(
            157504090,
            "Никита MTS",
            "DIALOG",
            max_user_id=42,
            username="nikita_mts",
            phone="+7 (999) 123-45-67",
            profile_link="max.ru/nikita_mts",
            participant_ids=(1, 42),
            contact_found=True,
        )

        for query in ("Никита", "nikita_mts", "9991234567", "42", "157504090", "@nikita_mts"):
            self.assertTrue(MaxUserService._chat_matches(chat, query.casefold()), query)
        self.assertFalse(MaxUserService._chat_matches(chat, "другой контакт"))

    def test_allowlist_uses_selected_account_and_exact_chat_id(self) -> None:
        with SessionLocal() as db:
            account = MaxAccount(
                user_id=1,
                label="personal",
                max_user_id=1,
                session_ciphertext="encrypted-session",
                status="active",
            )
            db.add(account)
            db.commit()
            db.refresh(account)
            account_id = account.id

        selected = MaxChat(
            157504090,
            "Никита MTS",
            "DIALOG",
            max_user_id=42,
            username="nikita_mts",
            phone="79991234567",
            participant_ids=(1, 42),
            contact_found=True,
        )

        class FakeApi:
            async def get_chats(self, client):
                return [selected, MaxChat(157504091, "Другой контакт", "DIALOG", max_user_id=43)]

        service = MaxUserService(self.settings, api=FakeApi())
        account_stub = SimpleNamespace(id=account_id, label="personal")

        @asynccontextmanager
        async def fake_client(user_id, account_id=None):
            self.assertEqual(account_id, account_stub.id)
            yield account_stub, SimpleNamespace(client=object())

        service._client = fake_client
        result = asyncio.run(service.add_allowed_chat(1, 157504090, account_id))

        self.assertEqual(result["account_id"], account_id)
        self.assertEqual(result["max_chat_id"], 157504090)
        self.assertEqual(result["max_user_id"], 42)
        with SessionLocal() as db:
            allowed = db.query(MaxAllowedChat).one()
            self.assertEqual(allowed.account_id, account_id)
            self.assertEqual(allowed.max_chat_id, 157504090)
            self.assertEqual(allowed.display_name, "Никита MTS")


if __name__ == "__main__":
    unittest.main()
