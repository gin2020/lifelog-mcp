"""Сервисный слой для работы с финансовыми событиями."""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.finance_event import FinanceEvent
from app.db.models.finance_item import FinanceItem
from app.schemas.finance import (
    FinanceEventCreate,
    FinanceEventUpdate,
    FinanceItemUpdate,
)
from app.services.notification_outbox_service import NotificationOutboxService


logger = logging.getLogger(__name__)


class FinanceService:
    """Provide ORM-backed operations for FinanceEvent records."""

    def __init__(self, session: Session) -> None:
        """Initialize the service with an active SQLAlchemy session."""
        self._session = session

    def create_event(
        self,
        user_id: int,
        payload: FinanceEventCreate,
    ) -> FinanceEvent:
        """Create and persist a new finance event."""
        logger.info(
            "FinanceService.create_event started: user_id=%s operation_type=%r",
            user_id,
            payload.operation_type,
        )

        try:
            now = datetime.now(timezone.utc)

            event = FinanceEvent(
                user_id=user_id,
                uuid=uuid4(),
                created_at=now,
                updated_at=now,
                operation_type=payload.operation_type,
                place=payload.place,
                currency=payload.currency,
                total_amount=payload.total_amount,
            )

            for item in payload.items:
                event.items.append(
                    FinanceItem(
                        name=item.name,
                        category=item.category,
                        quantity=item.quantity,
                        unit=item.unit,
                        total_price=item.total_price,
                    )
                )

            self._session.add(event)
            self._session.flush()
            NotificationOutboxService(self._session).enqueue_created(
                user_id=user_id,
                aggregate_type="finance_event",
                aggregate_id=event.id,
                event_type="finance_event.created",
            )
            self._session.commit()
            self._session.refresh(event)

            logger.info(
                "FinanceService.create_event completed: event_id=%s",
                event.id,
            )

            return event

        except Exception:
            logger.exception("FinanceService.create_event failed")

            try:
                self._session.rollback()
            except Exception:
                logger.exception("FinanceService.create_event rollback failed")

            raise

    def get_event(
        self,
        user_id: int,
        event_id: int,
    ) -> FinanceEvent | None:
        """Return an event by its primary key, if it exists."""
        statement = select(FinanceEvent).where(
            FinanceEvent.id == event_id,
            FinanceEvent.user_id == user_id,
        )
        return self._session.scalar(statement)

    def get_item(
        self,
        user_id: int,
        event_id: int,
        item_id: int,
    ) -> FinanceItem | None:
        """Return an item only when it belongs to the requested user's event."""
        statement = (
            select(FinanceItem)
            .join(FinanceEvent)
            .where(
                FinanceItem.id == item_id,
                FinanceItem.event_id == event_id,
                FinanceEvent.user_id == user_id,
            )
        )
        return self._session.scalar(statement)

    def update_event(
        self,
        user_id: int,
        event_id: int,
        changes: FinanceEventUpdate,
    ) -> FinanceEvent | None:
        """Update event metadata without changing its items or total."""
        event = self.get_event(user_id, event_id)
        if event is None:
            return None

        if changes.operation_type is not None:
            event.operation_type = changes.operation_type
        if changes.place is not None:
            event.place = changes.place
        if changes.currency is not None:
            event.currency = changes.currency
        event.updated_at = datetime.now(timezone.utc)

        try:
            NotificationOutboxService(self._session).enqueue_updated(
                user_id=user_id,
                aggregate_type="finance_event",
                aggregate_id=event.id,
                event_type="finance_event.updated",
            )
            self._session.commit()
            self._session.refresh(event)
            return event
        except Exception:
            self._session.rollback()
            raise

    def update_item(
        self,
        user_id: int,
        event_id: int,
        item_id: int,
        changes: FinanceItemUpdate,
    ) -> tuple[FinanceEvent, FinanceItem] | None:
        """Update one owned item and recalculate its event total atomically."""
        item = self.get_item(user_id, event_id, item_id)
        if item is None:
            return None

        event = item.event
        if changes.name is not None:
            item.name = changes.name
        if changes.category is not None:
            item.category = changes.category
        if changes.quantity is not None:
            item.quantity = changes.quantity
        if changes.unit is not None:
            item.unit = changes.unit
        if changes.total_price is not None:
            item.total_price = changes.total_price

        event.total_amount = self._items_total(event.items)
        event.updated_at = datetime.now(timezone.utc)

        try:
            NotificationOutboxService(self._session).enqueue_updated(
                user_id=user_id,
                aggregate_type="finance_event",
                aggregate_id=event.id,
                event_type="finance_event.updated",
            )
            self._session.commit()
            self._session.refresh(event)
            self._session.refresh(item)
            return event, item
        except Exception:
            self._session.rollback()
            raise

    def delete_item(
        self,
        user_id: int,
        event_id: int,
        item_id: int,
    ) -> FinanceEvent | None:
        """Delete one item and recalculate its event total atomically."""
        item = self.get_item(user_id, event_id, item_id)
        if item is None:
            return None

        event = item.event
        if len(event.items) == 1:
            raise ValueError(
                "Cannot delete the last finance item; delete the whole event instead"
            )

        remaining_total = self._items_total(
            [event_item for event_item in event.items if event_item.id != item_id]
        )
        self._session.delete(item)
        event.total_amount = remaining_total
        event.updated_at = datetime.now(timezone.utc)

        try:
            NotificationOutboxService(self._session).enqueue_deleted(
                user_id=user_id,
                aggregate_type="finance_event",
                aggregate_id=event.id,
                event_type="finance_event.item_deleted",
            )
            self._session.commit()
            self._session.refresh(event)
            return event
        except Exception:
            self._session.rollback()
            raise

    def list_events(
        self,
        user_id: int,
    ) -> list[FinanceEvent]:
        """Return all finance events ordered by creation time."""
        statement = select(FinanceEvent).where(
            FinanceEvent.user_id == user_id
        ).order_by(
            FinanceEvent.created_at, FinanceEvent.id
        )
        return list(self._session.scalars(statement))

    def delete_event(
        self,
        user_id: int,
        event_id: int,
    ) -> bool:
        """Delete an event and return whether it existed."""
        event = self.get_event(user_id, event_id)

        if event is None:
            return False

        try:
            self._session.delete(event)
            NotificationOutboxService(self._session).enqueue_deleted(
                user_id=user_id,
                aggregate_type="finance_event",
                aggregate_id=event_id,
                event_type="finance_event.deleted",
            )
            self._session.commit()
            return True
        except Exception:
            self._session.rollback()
            raise

    @staticmethod
    def _items_total(items: list[FinanceItem]) -> Decimal:
        """Calculate an event total from its item prices."""
        return sum(
            (item.total_price for item in items),
            Decimal("0"),
        )
