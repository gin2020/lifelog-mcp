"""Creation of safe, durable lifecycle events inside application transactions."""

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
        payload: dict[str, str | int] | None = None,
    ) -> NotificationOutbox:
        """Queue a privacy-safe notification for a newly persisted aggregate."""
        return self.enqueue_event(
            user_id=user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )

    def enqueue_updated(
        self,
        *,
        user_id: int,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str,
        payload: dict[str, str | int] | None = None,
    ) -> NotificationOutbox:
        """Queue a privacy-safe notification for an updated aggregate."""
        return self.enqueue_event(
            user_id=user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )

    def enqueue_deleted(
        self,
        *,
        user_id: int,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str,
        payload: dict[str, str | int] | None = None,
    ) -> NotificationOutbox:
        """Queue a privacy-safe notification for a deleted aggregate."""
        return self.enqueue_event(
            user_id=user_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )

    def enqueue_event(
        self,
        *,
        user_id: int,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str,
        payload: dict[str, str | int] | None = None,
    ) -> NotificationOutbox:
        """Add one lifecycle event to the caller's current transaction."""
        event = NotificationOutbox(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            user_id=user_id,
            payload={
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "event_type": event_type,
                **(payload or {}),
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
