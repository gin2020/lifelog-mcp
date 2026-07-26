"""Минимальные интеграционные тесты сервиса памяти."""

import unittest

from app.db.database import SessionLocal
from app.services.memory_service import MemoryService


class MemoryServiceTestCase(unittest.TestCase):
    """Проверяет CRUD-операции через публичный API MemoryService."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.service = MemoryService(self.session)
        self.created_ids: list[int] = []

    def tearDown(self) -> None:
        for memory_id in self.created_ids:
            self.service.delete_memory(memory_id)
        self.session.close()

    def create_memory(self, text: str = "Test memory") -> int:
        """Создаёт запись и регистрирует её для очистки после теста."""
        memory = self.service.create_memory(text, "test")
        self.created_ids.append(memory.id)
        return memory.id

    def test_create_memory(self) -> None:
        """Сервис создаёт запись с идентификатором и UUID."""
        memory_id = self.create_memory()

        memory = self.service.get_memory(memory_id)

        self.assertIsNotNone(memory)
        assert memory is not None
        self.assertEqual(memory.text, "Test memory")
        self.assertEqual(memory.memory_type, "test")
        self.assertIsNotNone(memory.uuid)
        self.assertIsNotNone(memory.created_at)
        self.assertIsNotNone(memory.updated_at)

    def test_get_memory(self) -> None:
        """Сервис возвращает запись по её первичному ключу."""
        memory_id = self.create_memory("Memory to get")

        memory = self.service.get_memory(memory_id)

        self.assertIsNotNone(memory)
        assert memory is not None
        self.assertEqual(memory.id, memory_id)
        self.assertEqual(memory.text, "Memory to get")

    def test_list_memories(self) -> None:
        """Сервис возвращает созданные записи в общем списке."""
        first_id = self.create_memory("First memory")
        second_id = self.create_memory("Second memory")

        memory_ids = {memory.id for memory in self.service.list_memories()}

        self.assertTrue({first_id, second_id}.issubset(memory_ids))

    def test_delete_memory(self) -> None:
        """Сервис удаляет запись и возвращает признак успешного удаления."""
        memory_id = self.create_memory("Memory to delete")

        deleted = self.service.delete_memory(memory_id)
        self.created_ids.remove(memory_id)

        self.assertTrue(deleted)
        self.assertIsNone(self.service.get_memory(memory_id))
