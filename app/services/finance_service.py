"""Сервисный слой для работы с финансовыми событиями."""

from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.finance_event import FinanceEvent
from app.db.models.finance_item import FinanceItem
from app.schemas.finance import FinanceEventCreate


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

        self._session.delete(event)
        self._session.commit()

        return True
