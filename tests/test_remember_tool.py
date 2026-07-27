"""Интеграционный тест высокоуровневого MCP-инструмента remember."""

import unittest
from uuid import UUID

from app.db.database import SessionLocal
from app.services.memory_service import MemoryService
from app.tools.remember import remember


class RememberToolTestCase(unittest.TestCase):
    """Проверяет сохранение структурированной памяти через remember."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.service = MemoryService(self.session)
        self.memory_id: int | None = None

    def tearDown(self) -> None:
        if self.memory_id is not None:
            self.service.delete_memory(self.memory_id)
        self.session.close()

    def test_remember_persists_structured_memory(self) -> None:
        """Tool возвращает JSON-совместимый ответ и сохраняет запись."""
        result = remember("Bought milk for 120 rubles", "finance")
        self.memory_id = result["id"]

        self.assertEqual(result["status"], "created")
        self.assertIsInstance(result["id"], int)
        UUID(result["uuid"])

        memory = self.service.get_memory(result["id"])
        self.assertIsNotNone(memory)
        assert memory is not None
        self.assertEqual(memory.text, "Bought milk for 120 rubles")
        self.assertEqual(memory.memory_type, "finance")
