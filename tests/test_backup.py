"""Unit tests for the standalone encrypted PostgreSQL backup job."""

import asyncio
import base64
from datetime import datetime
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr, ValidationError

from app.config.settings import Settings
from app.services.backup_crypto import decrypt_bytes, encrypt_bytes
from app.services.backup_service import (
    BackupError,
    BackupScheduler,
    BackupService,
    BackupTelegramUploader,
)


def make_key() -> str:
    """Return a valid test-only AES-256 key."""
    return base64.urlsafe_b64encode(b"backup-test-key-32-bytes-long!!!").decode()


class BackupTestCase(unittest.TestCase):
    """Verify backup primitives without external services."""

    def make_settings(self, directory: str, **updates) -> Settings:
        values = {
            "database_url": "postgresql+psycopg://lifelog:p%40ss%21word@db.example:5432/lifelog",
            "default_user_telegram_id": 123,
            "telegram_bot_token": SecretStr("telegram-secret-token"),
            "backup_enabled": True,
            "backup_encryption_key": SecretStr(make_key()),
            "backup_state_file": str(Path(directory) / "state.json"),
            "backup_lock_file": str(Path(directory) / "backup.lock"),
            "backup_timezone": "Europe/Berlin",
        }
        values.update(updates)
        return Settings(**values)

    def test_encryption_decryption_round_trip(self) -> None:
        plaintext = b"custom pg_dump bytes\x00" * 100
        key = SecretStr(make_key())
        encrypted = encrypt_bytes(plaintext, key)
        self.assertEqual(decrypt_bytes(encrypted, key), plaintext)
        self.assertNotEqual(encrypted, plaintext)

    def test_configuration_requires_key_when_enabled(self) -> None:
        with self.assertRaisesRegex(ValidationError, "BACKUP_ENCRYPTION_KEY"):
            Settings(
                database_url="postgresql://user:password@localhost/lifelog",
                default_user_telegram_id=1,
                backup_enabled=True,
            )

    def test_scheduler_uses_configured_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_settings(directory, backup_time="09:00")
            scheduler = BackupScheduler(settings, service=object())
            now = datetime(2026, 9, 28, 8, 30, tzinfo=ZoneInfo("Europe/Berlin"))
            self.assertEqual(
                scheduler.next_run_at(now),
                datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            )

    def test_database_url_password_is_decoded_only_for_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = BackupService(self.make_settings(directory), uploader=object())
            connection_uri, password = service._database_connection()
            self.assertEqual(connection_uri, "postgresql://lifelog@db.example:5432/lifelog")
            self.assertEqual(password, "p@ss!word")
            self.assertNotIn(password, connection_uri)

    def test_pg_dump_error_cleans_temporary_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_settings(directory)
            service = BackupService(settings, uploader=object())
            temporary_directories: list[Path] = []

            def fail_and_record(path: Path) -> None:
                temporary_directories.append(path.parent)
                raise BackupError("pg_dump executable was not found")

            with patch.object(service, "_run_pg_dump", side_effect=fail_and_record):
                with self.assertRaisesRegex(BackupError, "pg_dump"):
                    asyncio.run(
                        service.run_backup(
                            datetime(2026, 9, 28, 7, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                        )
                    )
            self.assertEqual(len(temporary_directories), 1)
            self.assertFalse(temporary_directories[0].exists())

    def test_success_upload_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_settings(directory)
            uploaded: list[bytes] = []

            class FakeUploader:
                async def upload(self, path: Path, _backup_date) -> None:
                    uploaded.append(path.read_bytes())

            service = BackupService(settings, uploader=FakeUploader())
            with patch.object(
                service,
                "_run_pg_dump",
                side_effect=lambda path: path.write_bytes(b"pg dump data" * 100),
            ):
                self.assertTrue(
                    asyncio.run(
                        service.run_backup(
                            datetime(2026, 9, 28, 7, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                        )
                    )
                )
            self.assertTrue(uploaded)
            self.assertTrue(Path(settings.backup_state_file).exists())
            self.assertFalse(list(Path(directory).glob("lifelog-*")))

            with patch.object(
                service,
                "_run_pg_dump",
                side_effect=AssertionError("a sent date must not run twice"),
            ):
                self.assertFalse(
                    asyncio.run(
                        service.run_backup(
                            datetime(2026, 9, 28, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                        )
                    )
                )

    def test_telegram_upload_uses_send_document_and_mock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.make_settings(directory)
            encrypted = Path(directory) / "lifelog_2026-09-28.backup.enc"
            encrypted.write_bytes(b"encrypted")
            requests = []

            class FakeClient:
                def __init__(self, **_kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

                async def post(self, url, **kwargs):
                    requests.append((url, kwargs))
                    return httpx.Response(200, json={"ok": True})

            with patch("app.services.backup_service.httpx.AsyncClient", FakeClient):
                asyncio.run(
                    BackupTelegramUploader(settings).upload(
                        encrypted, datetime(2026, 9, 28).date()
                    )
                )
            self.assertIn("/sendDocument", requests[0][0])
            self.assertEqual(requests[0][1]["data"]["chat_id"], "123")
            self.assertIn("document", requests[0][1]["files"])

    def test_secrets_are_not_written_to_backup_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token = "telegram-secret-token"
            key = make_key()
            settings = self.make_settings(directory)
            service = BackupService(settings, uploader=object())
            with self.assertLogs("app.services.backup_service", level=logging.ERROR) as logs:
                with patch.object(
                    service,
                    "_run_pg_dump",
                    side_effect=BackupError(f"failed {token} {key}"),
                ):
                    with self.assertRaises(BackupError):
                        asyncio.run(
                            service.run_backup(
                                datetime(2026, 9, 28, 7, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                            )
                        )
            output = "\n".join(logs.output)
            self.assertNotIn(token, output)
            self.assertNotIn(key, output)
