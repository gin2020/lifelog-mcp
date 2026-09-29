"""Интеграционные проверки редактирования и удаления MCP-данных."""

from decimal import Decimal
import unittest

from sqlalchemy import delete

from app.core.dependencies import get_default_user_id
from app.db.database import SessionLocal
from app.db.models.notification import NotificationOutbox
from app.db.models.finance_item import FinanceCategory
from app.schemas.finance import (
    FinanceEventCreate,
    FinanceEventUpdate,
    FinanceItemCreate,
    FinanceItemUpdate,
)
from app.schemas.memory import MemoryUpdate
from app.services.finance_service import FinanceService
from app.services.memory_service import MemoryService
from app.tools.finance import (
    delete_finance_event,
    delete_finance_item,
    update_finance_event,
    update_finance_item,
)
from app.tools.memory import delete_memory, update_memory


class EditDeleteToolsTestCase(unittest.TestCase):
    """Проверяет ownership и пересчёт суммы при изменении FinanceItem."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.user_id = get_default_user_id(self.session)
        self.event_ids: list[int] = []
        self.memory_ids: list[int] = []

    def tearDown(self) -> None:
        self.session.rollback()
        with SessionLocal() as session:
            if self.event_ids:
                session.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.aggregate_type == "finance_event",
                        NotificationOutbox.aggregate_id.in_(self.event_ids),
                    )
                )
            if self.memory_ids:
                session.execute(
                    delete(NotificationOutbox).where(
                        NotificationOutbox.aggregate_type == "memory",
                        NotificationOutbox.aggregate_id.in_(self.memory_ids),
                    )
                )
            for event_id in self.event_ids:
                event = FinanceService(session).get_event(self.user_id, event_id)
                if event is not None:
                    session.delete(event)
            for memory_id in self.memory_ids:
                memory = MemoryService(session).get_memory(self.user_id, memory_id)
                if memory is not None:
                    session.delete(memory)
            session.commit()
        self.session.close()

    def _create_event(self):
        event = FinanceService(self.session).create_event(
            self.user_id,
            FinanceEventCreate(
                operation_type="expense",
                place="test market",
                total_amount=Decimal("170.00"),
                items=[
                    FinanceItemCreate(
                        name="milk",
                        category=FinanceCategory.DAIRY,
                        quantity=Decimal("1"),
                        unit="pcs",
                        total_price=Decimal("120.00"),
                    ),
                    FinanceItemCreate(
                        name="apples",
                        category=FinanceCategory.FRUITS,
                        quantity=Decimal("1"),
                        unit="kg",
                        total_price=Decimal("50.00"),
                    ),
                ],
            ),
        )
        self.event_ids.append(event.id)
        self.session.refresh(event)
        return event

    def test_update_item_recalculates_event_total(self) -> None:
        event = self._create_event()
        milk = next(item for item in event.items if item.name == "milk")

        result = update_finance_item(
            event.id,
            milk.id,
            FinanceItemUpdate(total_price=Decimal("200.00")),
        )

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["total_amount"], "250.00")
        self.session.expire_all()
        stored = FinanceService(self.session).get_event(self.user_id, event.id)
        assert stored is not None
        self.assertEqual(stored.total_amount, Decimal("250.00"))

    def test_delete_item_recalculates_event_total(self) -> None:
        event = self._create_event()
        milk = next(item for item in event.items if item.name == "milk")

        result = delete_finance_item(event.id, milk.id)

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["total_amount"], "50.00")
        self.session.expire_all()
        stored = FinanceService(self.session).get_event(self.user_id, event.id)
        assert stored is not None
        self.assertEqual(stored.total_amount, Decimal("50.00"))
        self.assertEqual(len(stored.items), 1)

    def test_cannot_delete_last_item(self) -> None:
        event = self._create_event()
        apples = next(item for item in event.items if item.name == "apples")
        milk = next(item for item in event.items if item.name == "milk")

        delete_finance_item(event.id, milk.id)

        with self.assertRaisesRegex(ValueError, "last finance item"):
            delete_finance_item(event.id, apples.id)

        self.session.expire_all()
        stored = FinanceService(self.session).get_event(self.user_id, event.id)
        assert stored is not None
        self.assertEqual(stored.total_amount, Decimal("50.00"))

    def test_event_metadata_can_be_updated_and_event_deleted(self) -> None:
        event = self._create_event()

        updated = update_finance_event(
            event.id,
            FinanceEventUpdate(place="new market"),
        )
        self.assertEqual(updated["status"], "updated")

        deleted = delete_finance_event(event.id)
        self.assertEqual(deleted["status"], "deleted")
        self.assertIsNone(FinanceService(self.session).get_event(self.user_id, event.id))

    def test_memory_can_be_updated_and_deleted(self) -> None:
        memory = MemoryService(self.session).create_memory(
            self.user_id, "old text", "note"
        )
        self.memory_ids.append(memory.id)

        updated = update_memory(
            memory.id,
            MemoryUpdate(text="new text"),
        )
        self.assertEqual(updated["status"], "updated")
        self.session.expire_all()
        stored = MemoryService(self.session).get_memory(self.user_id, memory.id)
        assert stored is not None
        self.assertEqual(stored.text, "new text")

        deleted = delete_memory(memory.id)
        self.assertEqual(deleted["status"], "deleted")
        self.memory_ids.remove(memory.id)
        self.session.expire_all()
        self.assertIsNone(MemoryService(self.session).get_memory(self.user_id, memory.id))
