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
    """Process one bounded monitor per call; no message body is persisted or logged."""

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
                .order_by(TelegramDialogueMonitor.updated_at)
                .limit(1)
            ).first()
            if row is None:
                return False
            account, monitor, peer = row
            session = self._crypto.decrypt(account.session_ciphertext)
            user_id = account.user_id
            monitor_id = monitor.id
            peer_id = peer.telegram_peer_id
            watermark = monitor.last_notified_message_id or monitor.anchor_message_id

        client = self._api.client(session)
        try:
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
            await client.disconnect()

    @staticmethod
    def _advance_monitor(user_id: int, monitor_id: int, message_id: int, message_count: int) -> None:
        with SessionLocal() as db:
            monitor = db.scalar(select(TelegramDialogueMonitor).where(TelegramDialogueMonitor.id == monitor_id, TelegramDialogueMonitor.user_id == user_id))
            if monitor is not None:
                monitor.last_notified_message_id = max(monitor.last_notified_message_id or 0, message_id)
                monitor.updated_at = datetime.now(timezone.utc)
                db.add(NotificationOutbox(
                    aggregate_type="telegram_dialogue_monitor",
                    aggregate_id=monitor_id,
                    event_type="telegram.new_messages",
                    user_id=user_id,
                    payload={"aggregate_type": "telegram_dialogue_monitor", "aggregate_id": monitor_id, "event_type": "telegram.new_messages", "message_count": message_count},
                ))
                db.commit()

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
