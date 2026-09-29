"""Asynchronous delivery worker for durable notification outbox records."""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import and_, or_, select

from app.db.database import SessionLocal
from app.db.models.notification import NotificationOutbox, TelegramNotificationSubscription
from app.services.telegram_bot_service import (
    TelegramBotApiError,
    TelegramBotConfigurationError,
    TelegramBotService,
)


logger = logging.getLogger(__name__)
PROCESSING_LEASE = timedelta(minutes=5)
MAX_BACKOFF = timedelta(hours=1)


class NotificationDispatcher:
    """Claim and deliver outbox events without application business logic."""

    def __init__(self, telegram_bot: TelegramBotService | None = None) -> None:
        """Initialize the dispatcher with a Telegram delivery client."""
        self._telegram_bot = telegram_bot or TelegramBotService()

    async def run_once(self) -> bool:
        """Process at most one event and return whether work was claimed."""
        event_uuid = self._claim_next_event()
        if event_uuid is None:
            return False
        await self._deliver(event_uuid)
        return True

    def _claim_next_event(self):
        """Atomically lease one pending or abandoned processing event."""
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            event = session.scalar(
                select(NotificationOutbox)
                .where(
                    or_(
                        and_(
                            NotificationOutbox.status == "pending",
                            NotificationOutbox.next_retry_at <= now,
                        ),
                        and_(
                            NotificationOutbox.status == "processing",
                            NotificationOutbox.next_retry_at <= now,
                        ),
                    )
                )
                .order_by(NotificationOutbox.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return None
            event.status = "processing"
            event.attempts += 1
            event.next_retry_at = now + PROCESSING_LEASE
            session.commit()
            logger.info("Notification event claimed: event_uuid=%s attempts=%s", event.event_uuid, event.attempts)
            return event.event_uuid

    async def _deliver(self, event_uuid) -> None:
        """Send one claimed event or persist its terminal/retry outcome."""
        with SessionLocal() as session:
            event = session.get(NotificationOutbox, event_uuid)
            if event is None or event.status != "processing":
                return
            subscription = session.get(TelegramNotificationSubscription, event.user_id)
            if subscription is None or not subscription.is_enabled:
                event.status = "failed"
                event.last_error = "No active Telegram notification subscription"
                event.processed_at = datetime.now(timezone.utc)
                session.commit()
                logger.info("Notification event has no active subscription: event_uuid=%s", event_uuid)
                return
            chat_id = subscription.chat_id
            text = self._message_text(event)

        try:
            logger.info("Sending Telegram notification: event_uuid=%s", event_uuid)
            await self._telegram_bot.send_message(chat_id, text)
        except TelegramBotConfigurationError as error:
            self._mark_retry(event_uuid, str(error))
        except TelegramBotApiError as error:
            if error.status_code == 403:
                self._mark_blocked(event_uuid, str(error))
            elif error.status_code in {400, 401, 404}:
                self._mark_failed(event_uuid, str(error))
            else:
                self._mark_retry(event_uuid, str(error), error.retry_after)
        except Exception:
            logger.exception("Unexpected notification delivery error: event_uuid=%s", event_uuid)
            self._mark_retry(event_uuid, "Unexpected notification delivery error")
        else:
            self._mark_sent(event_uuid)

    @staticmethod
    def _message_text(event: NotificationOutbox) -> str:
        """Build a lifecycle confirmation without including user content."""
        action = {
            "created": "сохранена",
            "updated": "изменена",
            "deleted": "удалена",
            "item_deleted": "удалена",
        }.get(event.event_type.rsplit(".", maxsplit=1)[-1], "сохранена")
        return f"Lifelog: {action} запись {event.aggregate_type} #{event.aggregate_id}."

    def _mark_sent(self, event_uuid) -> None:
        """Persist successful delivery."""
        with SessionLocal() as session:
            event = session.get(NotificationOutbox, event_uuid)
            if event is None:
                return
            event.status = "sent"
            event.processed_at = datetime.now(timezone.utc)
            event.last_error = None
            session.commit()
        logger.info("Notification delivered: event_uuid=%s", event_uuid)

    def _mark_retry(self, event_uuid, error: str, retry_after: int | None = None) -> None:
        """Return a transient delivery failure to pending with exponential backoff."""
        with SessionLocal() as session:
            event = session.get(NotificationOutbox, event_uuid)
            if event is None:
                return
            delay = timedelta(seconds=retry_after) if retry_after else min(
                timedelta(minutes=1) * (2 ** min(event.attempts - 1, 6)), MAX_BACKOFF
            )
            event.status = "pending"
            event.next_retry_at = datetime.now(timezone.utc) + delay
            event.last_error = error
            session.commit()
        logger.warning("Notification scheduled for retry: event_uuid=%s error=%s", event_uuid, error)

    def _mark_failed(self, event_uuid, error: str) -> None:
        """Mark a permanent delivery failure as terminal."""
        with SessionLocal() as session:
            event = session.get(NotificationOutbox, event_uuid)
            if event is None:
                return
            event.status = "failed"
            event.last_error = error
            event.processed_at = datetime.now(timezone.utc)
            session.commit()
        logger.error("Notification delivery failed: event_uuid=%s error=%s", event_uuid, error)

    def _mark_blocked(self, event_uuid, error: str) -> None:
        """Disable an unreachable subscription and terminate its event."""
        with SessionLocal() as session:
            event = session.get(NotificationOutbox, event_uuid)
            if event is None:
                return
            subscription = session.get(TelegramNotificationSubscription, event.user_id)
            if subscription is not None:
                subscription.is_enabled = False
            event.status = "failed"
            event.last_error = error
            event.processed_at = datetime.now(timezone.utc)
            session.commit()
        logger.warning("Telegram subscription disabled after bot block: event_uuid=%s", event_uuid)
