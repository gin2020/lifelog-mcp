"""Проверки распространения ошибок в MemoryService."""

import unittest
from unittest.mock import Mock

from app.services.memory_service import MemoryService


class MemoryServiceErrorTestCase(unittest.TestCase):
    """Проверяет, что ошибки сохранения не маскируются успешным ответом."""

    def test_create_memory_reraises_commit_error_after_rollback(self) -> None:
        """Ошибка commit откатывается и возвращается вызывающему коду."""
        session = Mock()
        session.commit.side_effect = RuntimeError("commit failed")
        service = MemoryService(session)

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            service.create_memory(1, "test", "test")

        self.assertEqual(session.add.call_count, 2)
        session.commit.assert_called_once()
        session.refresh.assert_not_called()
        session.rollback.assert_called_once()
