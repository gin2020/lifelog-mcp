"""Creation of safe, durable notification events inside application transactions."""

import logging

from sqlalchemy.orm import Session

from app.db.models.notification import NotificationOutbox


logger = logging.getLogger(__name__)


class NotificationOutboxService:
    """Add channel-independent notification events to the current transaction."""

    def __init__(self, session: Session) -> None:
        """Initialize the service with the caller's active transaction."""
        self._session = session

    def enqueue_created(
        self,
        *,
        user_id: int,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str,
    ) -> NotificationOutbox:
        """Queue a privacy-safe notification for a newly persisted aggregate."""
        event = NotificationOutbox(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            user_id=user_id,
            payload={
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
            },
        )
        self._session.add(event)
        logger.info(
            "Notification outbox event created: event_type=%s aggregate=%s:%s user_id=%s",
            event_type,
            aggregate_type,
            aggregate_id,
            user_id,
        )
        return event
