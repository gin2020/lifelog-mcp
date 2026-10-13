"""Polling worker for bounded, allowlisted MAX personal chats."""

from datetime import datetime, timezone
import logging
import asyncio

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.max_user import MaxAccount, MaxAllowedChat, MaxChatMonitor
from app.db.models.notification import NotificationOutbox
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxUserApi, MaxUserApiError


logger = logging.getLogger(__name__)


class MaxUserWorker:
    """Process one fairly scheduled MAX monitor per call."""

    def __init__(self, settings: Settings | None = None, api: MaxUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or MaxUserApi(self._settings)
        self._crypto = MaxSessionCrypto(self._settings)

    async def run_once(self) -> bool:
        with SessionLocal() as db:
            row = db.execute(
                select(MaxAccount, MaxChatMonitor, MaxAllowedChat)
                .join(MaxChatMonitor, MaxChatMonitor.account_id == MaxAccount.id)
                .join(MaxAllowedChat, MaxAllowedChat.id == MaxChatMonitor.allowed_chat_id)
                .where(
                    MaxAccount.status == "active",
                    MaxAccount.session_ciphertext.is_not(None),
                    MaxChatMonitor.is_active.is_(True),
                    MaxAllowedChat.is_enabled.is_(True),
                )
                .order_by(MaxChatMonitor.updated_at, MaxChatMonitor.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).first()
            if row is None:
                return False
            account, monitor, chat = row
            session_ciphertext = account.session_ciphertext
            user_id = account.user_id
            account_id = account.id
            account_label = account.label
            device_id = account.device_id
            max_user_id = account.max_user_id
            monitor_id = monitor.id
            chat_id = chat.max_chat_id
            display_name = chat.display_name or f"MAX chat {chat_id}"
            watermark = max((value for value in (monitor.anchor_message_id, monitor.last_notified_message_id) if value is not None), default=None)
            monitor.updated_at = datetime.now(timezone.utc)
            db.commit()

        handle = None
        try:
            handle = self._api.create_client(self._crypto.decrypt(session_ciphertext), device_id=device_id)
            await self._api.connect(handle)
            messages = await self._api.get_history(handle.client, chat_id, self._settings.max_max_messages_per_request)
            incoming = [message for message in messages if not self._is_outgoing(message, max_user_id) and isinstance(message.get("id"), int) and (watermark is None or message["id"] > watermark)]
            if incoming:
                self._advance_monitor(user_id, monitor_id, max(message["id"] for message in incoming), len(incoming), display_name, account_id, account_label)
            self._mark_sync(user_id, account_id)
            return True
        except MaxUserApiError as error:
            self._mark_account_error(user_id, account_id, str(error))
            logger.warning("MAX User API monitor failed: user_id=%s monitor_id=%s", user_id, monitor_id)
            return True
        except Exception:
            self._mark_account_error(user_id, account_id, "Unexpected MAX User API worker error")
            logger.exception("MAX User API monitor failed unexpectedly: user_id=%s monitor_id=%s", user_id, monitor_id)
            return True
        finally:
            if handle is not None:
                session_blob = handle.read_session()
                await handle.close()
                if session_blob:
                    self._save_session(user_id, account_id, session_blob)

    async def run_forever(self, stop_event: asyncio.Event, refresh_seconds: float = 10.0) -> None:
        """Keep one WebSocket client per monitored account and consume realtime events."""
        live: dict[int, tuple[object, object, int, int]] = {}
        try:
            while not stop_event.is_set():
                accounts = self._monitored_accounts()
                for account_id, user_id, session_ciphertext, device_id in accounts:
                    if account_id in live:
                        continue
                    try:
                        handle = self._api.create_client(self._crypto.decrypt(session_ciphertext), device_id=device_id)

                        @handle.client.on_message()
                        async def _on_message(message, _client, *, _account_id=account_id, _user_id=user_id):
                            await self._handle_realtime_message(_user_id, _account_id, message)

                        task = asyncio.create_task(self._api.start(handle), name=f"max-ws-{account_id}")
                        live[account_id] = (handle, task, user_id, account_id)
                    except Exception:
                        self._mark_account_error(user_id, account_id, "MAX realtime client could not be started")
                        logger.exception("MAX realtime client startup failed: account_id=%s", account_id)

                for account_id, (handle, task, user_id, _) in list(live.items()):
                    if task.done():
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as error:
                            self._mark_account_error(user_id, account_id, str(error))
                            logger.warning("MAX realtime client stopped: account_id=%s", account_id)
                        session_blob = handle.read_session()
                        await handle.close()
                        if session_blob:
                            self._save_session(user_id, account_id, session_blob)
                        del live[account_id]
                    else:
                        session_blob = handle.read_session()
                        if session_blob:
                            self._save_session(user_id, account_id, session_blob)

                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            for handle, task, user_id, account_id in live.values():
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self._mark_account_error(user_id, account_id, "MAX realtime client stopped")
                await handle.close()

    async def _handle_realtime_message(self, user_id: int, account_id: int, message: object) -> None:
        normalized = self._api.message_from_object(message)
        chat_id = normalized.get("chat_id")
        message_id = normalized.get("id")
        with SessionLocal() as db:
            max_user_id = db.scalar(select(MaxAccount.max_user_id).where(MaxAccount.user_id == user_id, MaxAccount.id == account_id))
        if self._is_outgoing(normalized, max_user_id) or not isinstance(chat_id, int) or not isinstance(message_id, int):
            return
        with SessionLocal() as db:
            rows = list(db.execute(
                select(MaxChatMonitor, MaxAllowedChat)
                .join(MaxAllowedChat, MaxAllowedChat.id == MaxChatMonitor.allowed_chat_id)
                .where(
                    MaxChatMonitor.user_id == user_id,
                    MaxChatMonitor.account_id == account_id,
                    MaxChatMonitor.is_active.is_(True),
                    MaxAllowedChat.is_enabled.is_(True),
                    MaxAllowedChat.max_chat_id == chat_id,
                )
            ))
        for monitor, chat in rows:
            self._advance_monitor(user_id, monitor.id, message_id, 1, chat.display_name or f"MAX chat {chat_id}", account_id, self._account_label(account_id))

    @staticmethod
    def _is_outgoing(message: dict[str, object], max_user_id: int | None) -> bool:
        return bool(message.get("out")) or (max_user_id is not None and message.get("sender_id") == max_user_id)

    @staticmethod
    def _monitored_accounts() -> list[tuple[int, int, str, str | None]]:
        with SessionLocal() as db:
            rows = db.execute(
                select(MaxAccount.id, MaxAccount.user_id, MaxAccount.session_ciphertext, MaxAccount.device_id)
                .join(MaxChatMonitor, MaxChatMonitor.account_id == MaxAccount.id)
                .join(MaxAllowedChat, MaxAllowedChat.id == MaxChatMonitor.allowed_chat_id)
                .where(MaxAccount.status == "active", MaxAccount.session_ciphertext.is_not(None), MaxChatMonitor.is_active.is_(True), MaxAllowedChat.is_enabled.is_(True))
                .distinct()
            )
            return [(account_id, user_id, session, device_id) for account_id, user_id, session, device_id in rows if isinstance(session, str)]

    @staticmethod
    def _account_label(account_id: int) -> str:
        with SessionLocal() as db:
            label = db.scalar(select(MaxAccount.label).where(MaxAccount.id == account_id))
            return label or "legacy"

    @staticmethod
    def _advance_monitor(user_id: int, monitor_id: int, message_id: int, message_count: int, display_name: str, account_id: int, account_label: str) -> bool:
        with SessionLocal() as db:
            monitor = db.scalar(select(MaxChatMonitor).where(MaxChatMonitor.id == monitor_id, MaxChatMonitor.user_id == user_id, MaxChatMonitor.is_active.is_(True)).with_for_update())
            if monitor is None:
                return False
            current = max((value for value in (monitor.anchor_message_id, monitor.last_notified_message_id) if value is not None), default=None)
            if current is not None and message_id <= current:
                return False
            monitor.last_notified_message_id = message_id
            monitor.updated_at = datetime.now(timezone.utc)
            db.add(NotificationOutbox(
                aggregate_type="max_chat_monitor",
                aggregate_id=monitor_id,
                event_type="max.new_messages",
                user_id=user_id,
                payload={
                    "aggregate_type": "max_chat_monitor",
                    "aggregate_id": monitor_id,
                    "event_type": "max.new_messages",
                    "message_count": message_count,
                    "last_message_id": message_id,
                    "display_name": display_name,
                    "account_id": account_id,
                    "account_label": account_label,
                },
            ))
            db.commit()
            return True

    @staticmethod
    def _mark_sync(user_id: int, account_id: int) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.user_id == user_id, MaxAccount.id == account_id))
            if account is not None:
                account.last_sync_at = datetime.now(timezone.utc)
                account.last_error = None
                db.commit()

    @staticmethod
    def _mark_account_error(user_id: int, account_id: int, error: str) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.user_id == user_id, MaxAccount.id == account_id))
            if account is not None:
                account.last_error = error[:1000]
                db.commit()

    def _save_session(self, user_id: int, account_id: int, session_blob: bytes) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.user_id == user_id, MaxAccount.id == account_id))
            if account is not None:
                account.session_ciphertext = self._crypto.encrypt(session_blob)
                db.commit()
