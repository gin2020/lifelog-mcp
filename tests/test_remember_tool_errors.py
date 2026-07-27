"""Проверки распространения ошибок из MCP-инструмента remember."""

import unittest
from unittest.mock import patch

from app.tools.remember import remember


class RememberToolErrorTestCase(unittest.TestCase):
    """Проверяет, что remember не возвращает ложный успешный результат."""

    def test_remember_reraises_session_error(self) -> None:
        """Ошибка открытия сессии передаётся в MCP без успешного JSON-ответа."""
        with patch("app.tools.remember.SessionLocal", side_effect=RuntimeError("DB unavailable")):
            with self.assertRaisesRegex(RuntimeError, "DB unavailable"):
                remember("test", "test")
