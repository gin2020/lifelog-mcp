"""Интеграционные проверки изоляции данных по владельцу."""

from decimal import Decimal
import unittest

from app.db.database import SessionLocal
from app.db.models.finance_item import FinanceCategory
from app.db.models.notification import NotificationOutbox
from app.db.models.user import User
from app.db.repositories.finance_repository import FinanceRepository
from app.schemas.finance import (
    FinanceEventCreate,
    FinanceEventUpdate,
    FinanceItemCreate,
    FinanceItemUpdate,
)
from app.services.finance_service import FinanceService
from app.services.memory_service import MemoryService


class UserScopingTestCase(unittest.TestCase):
    """Проверяет, что сервисы и репозиторий не пересекают владельцев."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.session.add_all([User(), User()])
        self.session.commit()
        self.users = list(self.session.query(User).order_by(User.id.desc()).limit(2))
        self.first_user, self.second_user = self.users
        self.memory_service = MemoryService(self.session)
        self.finance_service = FinanceService(self.session)
        self.memory_ids: list[int] = []
        self.event_ids: list[int] = []

    def tearDown(self) -> None:
        for memory_id in self.memory_ids:
            self.memory_service.delete_memory(self.first_user.id, memory_id)
            self.memory_service.delete_memory(self.second_user.id, memory_id)
        for event_id in self.event_ids:
            self.finance_service.delete_event(self.first_user.id, event_id)
            self.finance_service.delete_event(self.second_user.id, event_id)
        self.session.execute(
            NotificationOutbox.__table__.delete().where(
                NotificationOutbox.user_id.in_([self.first_user.id, self.second_user.id])
            )
        )
        self.session.delete(self.first_user)
        self.session.delete(self.second_user)
        self.session.commit()
        self.session.close()

    def test_memory_operations_are_scoped_to_owner(self) -> None:
        """Другой пользователь не может прочитать или удалить memory."""
        memory = self.memory_service.create_memory(
            self.first_user.id, "private memory", "test"
        )
        self.memory_ids.append(memory.id)

        self.assertIsNone(self.memory_service.get_memory(self.second_user.id, memory.id))
        self.assertFalse(self.memory_service.delete_memory(self.second_user.id, memory.id))
        self.assertEqual(
            [item.id for item in self.memory_service.list_memories(self.second_user.id)],
            [],
        )

    def test_finance_queries_are_scoped_to_owner(self) -> None:
        """Финансовый репозиторий не включает позиции другого пользователя."""
        event = self.finance_service.create_event(
            self.first_user.id,
            FinanceEventCreate(
                operation_type="expense",
                total_amount=Decimal("99.00"),
                items=[
                    FinanceItemCreate(
                        name="private purchase",
                        category=FinanceCategory.OTHER,
                        quantity=Decimal("1"),
                        unit="pcs",
                        total_price=Decimal("99.00"),
                    )
                ],
            ),
        )
        self.event_ids.append(event.id)

        other_repository = FinanceRepository(self.session, self.second_user.id)
        own_repository = FinanceRepository(self.session, self.first_user.id)

        self.assertEqual(other_repository.get_total_amount(), Decimal("0"))
        self.assertEqual(own_repository.get_total_amount(), Decimal("99.00"))
        self.assertIsNone(self.finance_service.get_event(self.second_user.id, event.id))
        item = event.items[0]
        self.assertIsNone(
            self.finance_service.update_event(
                self.second_user.id,
                event.id,
                FinanceEventUpdate(place="foreign market"),
            )
        )
        self.assertIsNone(
            self.finance_service.update_item(
                self.second_user.id,
                event.id,
                item.id,
                FinanceItemUpdate(total_price=Decimal("1.00")),
            )
        )
        self.assertIsNone(
            self.finance_service.delete_item(
                self.second_user.id,
                event.id,
                item.id,
            )
        )
