"""Regression tests for Telegram Bot API secret redaction."""

import asyncio
from datetime import date
from io import StringIO
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from app.config.logging import configure_logging
from app.config.settings import Settings
from app.services.telegram_bot_service import TelegramBotApiError, TelegramBotService
from app.utils.redaction import redact_secrets


TEST_TOKEN = "123456789:TEST_bot-token_0123456789"


def make_settings() -> Settings:
    """Build settings containing only fictional test credentials."""
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        default_user_telegram_id=123,
        telegram_bot_token=SecretStr(TEST_TOKEN),
    )


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.is_success = 200 <= status_code < 300

    def json(self) -> dict:
        return self._body


class FakeClient:
    response: FakeResponse | None = None
    error: Exception | None = None
    last_url: str | None = None

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url: str, **_kwargs):
        self.last_url = url
        FakeClient.last_url = url
        logger = logging.getLogger("httpx")
        logger.info("HTTP request method=POST url=%s", url)
        if self.error is not None:
            logger.exception("retry attempt failed url=%s", url)
            raise self.error
        return self.response


class TelegramLogRedactionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        configure_logging()
        FakeClient.response = None
        FakeClient.error = None
        FakeClient.last_url = None
        self.settings = make_settings()
        self.service = TelegramBotService(self.settings)
        self.stream = StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.logger = logging.getLogger("telegram-redaction-test")
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.close()

    def output(self) -> str:
        return self.stream.getvalue()

    def test_successful_request_logs_safe_url_and_status_without_token(self) -> None:
        FakeClient.response = FakeResponse(200, {"ok": True})
        with patch("app.services.telegram_bot_service.httpx.AsyncClient", FakeClient):
            asyncio.run(self.service.send_message(123, "hello"))
        self.logger.info("Telegram API request completed status=%s", 200)
        output = self.output()
        self.assertNotIn(TEST_TOKEN, output)
        self.assertIn("status=200", output)

    def test_http_error_logs_type_and_status_without_token(self) -> None:
        FakeClient.response = FakeResponse(
            500,
            {
                "ok": False,
                "description": f"failed https://api.telegram.org/bot{TEST_TOKEN}/sendMessage",
            },
        )
        with patch("app.services.telegram_bot_service.httpx.AsyncClient", FakeClient):
            with self.assertRaises(TelegramBotApiError):
                asyncio.run(self.service.send_message(123, "hello"))
        error = TelegramBotApiError(500, f"HTTP failure for {TEST_TOKEN}")
        self.logger.error(
            "Telegram API request failed status=%s error_type=%s error=%s",
            error.status_code,
            type(error).__name__,
            error,
        )
        output = self.output()
        self.assertNotIn(TEST_TOKEN, output)
        self.assertIn("status=500", output)
        self.assertIn("TelegramBotApiError", output)

    def test_http_client_exception_traceback_and_retry_are_redacted(self) -> None:
        url = f"https://api.telegram.org/bot{TEST_TOKEN}/getUpdates"
        FakeClient.error = httpx.ConnectError(f"connect failed for {url}")
        with patch("app.services.telegram_bot_service.httpx.AsyncClient", FakeClient):
            with self.assertRaises(TelegramBotApiError):
                asyncio.run(self.service.send_message(123, "hello"))
        try:
            raise RuntimeError(f"retry failed for {url}")
        except RuntimeError:
            self.logger.exception("retry attempt failed error_type=%s", "HTTPError")
        output = self.output()
        self.assertNotIn(TEST_TOKEN, output)
        self.assertIn("retry attempt failed", output)
        self.assertIn("Traceback", output)
        self.assertIn("HTTPError", output)

    def test_telegram_url_redaction_handles_raw_and_encoded_path_tokens(self) -> None:
        encoded = TEST_TOKEN.replace(":", "%3A")
        samples = (
            f"https://api.telegram.org/bot{TEST_TOKEN}/sendMessage",
            f"https://api.telegram.org/bot{encoded}/getUpdates",
            f"api.telegram.org/bot{TEST_TOKEN}/sendDocument?chat_id=123",
        )
        for sample in samples:
            safe = redact_secrets(sample)
            self.assertNotIn(TEST_TOKEN, safe)
            self.assertNotIn(encoded, safe)
            self.assertIn("[REDACTED]", safe)

    def test_backup_http_error_does_not_keep_token_in_exception_chain(self) -> None:
        from app.services.backup_service import BackupTelegramError, BackupTelegramUploader

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.enc"
            path.write_bytes(b"encrypted")
            FakeClient.error = httpx.ConnectError(
                f"request failed https://api.telegram.org/bot{TEST_TOKEN}/sendDocument"
            )
            with patch("app.services.backup_service.httpx.AsyncClient", FakeClient):
                with self.assertRaises(BackupTelegramError) as context:
                    asyncio.run(BackupTelegramUploader(self.settings).upload(path, date.today()))
            self.assertIsNone(context.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
