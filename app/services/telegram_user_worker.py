"""Polling worker for bounded, allowlisted Telegram user dialogues."""

from datetime import datetime, timezone
import logging

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.notification import NotificationOutbox
from app.db.models.telegram_user import TelegramAccount, TelegramAllowedPeer, TelegramDialogueMonitor
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApi, TelegramUserApiError


logger = logging.getLogger(__name__)


class TelegramUserWorker:
    """Process one fairly scheduled monitor per call.

    ``last_notified_message_id`` is the worker's notification cursor. It is
    intentionally independent from ``last_read_message_id``, which belongs to
    the manual ``telegram_get_new_messages`` read API.
    """

    def __init__(self, settings: Settings | None = None, api: TelegramUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or TelegramUserApi(self._settings)
        self._crypto = TelegramSessionCrypto(self._settings)

    async def run_once(self) -> bool:
        with SessionLocal() as db:
            row = db.execute(
                select(TelegramAccount, TelegramDialogueMonitor, TelegramAllowedPeer)
                .join(TelegramDialogueMonitor, TelegramDialogueMonitor.user_id == TelegramAccount.user_id)
                .join(TelegramAllowedPeer, TelegramAllowedPeer.id == TelegramDialogueMonitor.allowed_peer_id)
                .where(
                    TelegramAccount.status == "active",
                    TelegramAccount.session_ciphertext.is_not(None),
                    TelegramDialogueMonitor.is_active.is_(True),
                    TelegramAllowedPeer.is_enabled.is_(True),
                )
                .order_by(TelegramDialogueMonitor.updated_at, TelegramDialogueMonitor.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).first()
            if row is None:
                return False
            account, monitor, peer = row
            session_ciphertext = account.session_ciphertext
            user_id = account.user_id
            monitor_id = monitor.id
            peer_id = peer.telegram_peer_id
            watermark = self._notification_watermark(monitor)
            # Claim the scheduling turn before network I/O. This both rotates
            # monitors that have no messages and prevents concurrent workers
            # from repeatedly selecting the same row.
            monitor.updated_at = datetime.now(timezone.utc)
            db.commit()

        client = None
        try:
            session = self._crypto.decrypt(session_ciphertext)
            client = self._api.client(session)
            await client.connect()
            if not await client.is_user_authorized():
                self._mark_account_error(user_id, "Telegram session is no longer authorized")
                return True
            messages = await self._api.get_messages(client, peer_id, self._settings.telegram_max_messages_per_request, watermark)
            incoming = [message for message in messages if not message.get("out") and isinstance(message.get("id"), int)]
            if incoming:
                self._advance_monitor(user_id, monitor_id, max(message["id"] for message in incoming), len(incoming))
            self._mark_sync(user_id)
            return True
        except TelegramUserApiError as error:
            self._mark_account_error(user_id, str(error))
            logger.warning("Telegram User API monitor failed: user_id=%s monitor_id=%s", user_id, monitor_id)
            return True
        except Exception:
            self._mark_account_error(user_id, "Unexpected Telegram User API worker error")
            logger.exception("Telegram User API monitor failed unexpectedly: user_id=%s monitor_id=%s", user_id, monitor_id)
            return True
        finally:
            if client is not None:
                await client.disconnect()

    @staticmethod
    def _advance_monitor(user_id: int, monitor_id: int, message_id: int, message_count: int) -> bool:
        """Atomically advance the notification cursor and enqueue one event.

        The row lock makes concurrent worker runs idempotent: only the run that
        observes a message ID greater than the stored cursor creates the
        outbox event. Manual reads never update this cursor.
        """
        with SessionLocal() as db:
            monitor = db.scalar(
                select(TelegramDialogueMonitor)
                .where(
                    TelegramDialogueMonitor.id == monitor_id,
                    TelegramDialogueMonitor.user_id == user_id,
                    TelegramDialogueMonitor.is_active.is_(True),
                )
                .with_for_update()
            )
            if monitor is None:
                return False
            current_watermark = TelegramUserWorker._notification_watermark(monitor) or 0
            if message_id <= current_watermark:
                return False
            monitor.last_notified_message_id = message_id
            monitor.updated_at = datetime.now(timezone.utc)
            db.add(NotificationOutbox(
                aggregate_type="telegram_dialogue_monitor",
                aggregate_id=monitor_id,
                event_type="telegram.new_messages",
                user_id=user_id,
                payload={
                    "aggregate_type": "telegram_dialogue_monitor",
                    "aggregate_id": monitor_id,
                    "event_type": "telegram.new_messages",
                    "message_count": message_count,
                    "last_message_id": message_id,
                },
            ))
            db.commit()
            return True

    @staticmethod
    def _notification_watermark(monitor: TelegramDialogueMonitor) -> int | None:
        """Return the worker cursor without consulting the manual-read cursor."""
        return max(
            (value for value in (monitor.anchor_message_id, monitor.last_notified_message_id) if value is not None),
            default=None,
        )

    @staticmethod
    def _mark_sync(user_id: int) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id))
            if account is not None:
                account.last_sync_at = datetime.now(timezone.utc)
                account.last_error = None
                db.commit()

    @staticmethod
    def _mark_account_error(user_id: int, error: str) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id))
            if account is not None:
                account.last_error = error[:1000]
                db.commit()
