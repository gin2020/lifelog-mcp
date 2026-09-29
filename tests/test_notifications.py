"""Integration tests for durable Telegram notification infrastructure."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from pydantic import SecretStr
from sqlalchemy import delete, select

from app.config.settings import get_settings
from app.core.dependencies import get_default_user_id
from app.core.telegram_webhook_routes import telegram_webhook
from app.db.database import SessionLocal
from app.db.models.finance_item import FinanceCategory
from app.db.models.notification import NotificationOutbox, TelegramNotificationSubscription
from app.db.models.user import User
from app.schemas.finance import (
    FinanceEventCreate,
    FinanceEventUpdate,
    FinanceItemCreate,
    FinanceItemUpdate,
)
from app.schemas.memory import MemoryUpdate
from app.services.finance_service import FinanceService
from app.services.memory_service import MemoryService
from app.services.notification_dispatcher import NotificationDispatcher
from app.services.notification_outbox_service import NotificationOutboxService
from app.services.telegram_bot_service import TelegramBotApiError
from starlette.requests import Request


class FakeTelegramBot:
    """In-memory Telegram client for deterministic dispatcher tests."""

    def __init__(self, error: TelegramBotApiError | None = None) -> None:
        self.error = error
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        """Record a sent message or raise the configured Telegram error."""
        if self.error is not None:
            raise self.error
        self.messages.append((chat_id, text))


class NotificationInfrastructureTestCase(unittest.TestCase):
    """Verifies outbox atomicity, delivery state and Telegram command consent."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.user_id = get_default_user_id(self.session)
        self.event_uuids = []
        self.memory_ids: list[int] = []
        self.finance_ids: list[int] = []

    def tearDown(self) -> None:
        self.session.rollback()
        with SessionLocal() as session:
            if self.event_uuids:
                session.execute(
                    delete(NotificationOutbox).where(NotificationOutbox.event_uuid.in_(self.event_uuids))
                )
            for memory_id in self.memory_ids:
                memory = MemoryService(session).get_memory(self.user_id, memory_id)
                if memory is not None:
                    session.delete(memory)
            for event_id in self.finance_ids:
                event = FinanceService(session).get_event(self.user_id, event_id)
                if event is not None:
                    session.delete(event)
            subscription = session.get(TelegramNotificationSubscription, self.user_id)
            if subscription is not None:
                session.delete(subscription)
            session.commit()
        self.session.close()

    def _track_outbox_for(
        self,
        aggregate_type: str,
        aggregate_id: int,
        event_type: str | None = None,
    ) -> NotificationOutbox:
        """Load and register the newest outbox event for an aggregate."""
        filters = [
            NotificationOutbox.aggregate_type == aggregate_type,
            NotificationOutbox.aggregate_id == aggregate_id,
        ]
        if event_type is not None:
            filters.append(NotificationOutbox.event_type == event_type)
        event = self.session.scalar(
            select(NotificationOutbox)
            .where(*filters)
            .order_by(NotificationOutbox.created_at.desc())
            .limit(1)
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.event_uuids.append(event.event_uuid)
        return event

    def test_memory_creation_commits_safe_outbox_event(self) -> None:
        """Memory and its notification event are committed together."""
        memory = MemoryService(self.session).create_memory(self.user_id, "private text", "test")
        self.memory_ids.append(memory.id)

        event = self._track_outbox_for("memory", memory.id)

        self.assertEqual(event.status, "pending")
        self.assertEqual(event.event_type, "memory.created")
        self.assertEqual(event.payload["aggregate_id"], memory.id)
        self.assertNotIn("private text", str(event.payload))

    def test_finance_creation_commits_safe_outbox_event(self) -> None:
        """FinanceEvent and its notification event are committed together."""
        event = FinanceService(self.session).create_event(
            self.user_id,
            FinanceEventCreate(
                operation_type="expense",
                total_amount=Decimal("99.00"),
                items=[
                    FinanceItemCreate(
                        name="sensitive product",
                        category=FinanceCategory.OTHER,
                        quantity=Decimal("1"),
                        unit="pcs",
                        total_price=Decimal("99.00"),
                    )
                ],
            ),
        )
        self.finance_ids.append(event.id)

        outbox = self._track_outbox_for("finance_event", event.id)

        self.assertEqual(outbox.event_type, "finance_event.created")
        self.assertNotIn("sensitive product", str(outbox.payload))
        self.assertNotIn("99", str(outbox.payload))

    def test_dispatcher_uses_lifecycle_action_in_message(self) -> None:
        """Dispatcher text distinguishes creation, update and deletion."""
        cases = {
            "memory.created": "Lifelog: сохранена запись memory #42.",
            "memory.updated": "Lifelog: изменена запись memory #42.",
            "memory.deleted": "Lifelog: удалена запись memory #42.",
            "finance_event.created": "Lifelog: сохранена запись finance_event #42.",
            "finance_event.updated": "Lifelog: изменена запись finance_event #42.",
            "finance_event.item_deleted": (
                "Lifelog: удалена запись finance_event #42."
            ),
            "finance_event.deleted": "Lifelog: удалена запись finance_event #42.",
        }
        for event_type, expected in cases.items():
            with self.subTest(event_type=event_type):
                event = NotificationOutbox(
                    aggregate_type=event_type.split(".", maxsplit=1)[0],
                    aggregate_id=42,
                    event_type=event_type,
                    user_id=self.user_id,
                    payload={},
                )
                self.assertEqual(NotificationDispatcher._message_text(event), expected)

    def test_memory_update_and_delete_enqueue_lifecycle_events(self) -> None:
        """Memory changes and deletion are atomically represented in outbox."""
        memory = MemoryService(self.session).create_memory(
            self.user_id, "private text", "test"
        )
        self.memory_ids.append(memory.id)

        updated = MemoryService(self.session).update_memory(
            self.user_id,
            memory.id,
            MemoryUpdate(text="changed text"),
        )
        self.assertIsNotNone(updated)
        update_event = self._track_outbox_for(
            "memory", memory.id, "memory.updated"
        )
        self.assertEqual(update_event.user_id, self.user_id)

        self.assertTrue(MemoryService(self.session).delete_memory(self.user_id, memory.id))
        delete_event = self._track_outbox_for(
            "memory", memory.id, "memory.deleted"
        )
        self.assertEqual(delete_event.user_id, self.user_id)

    def test_finance_event_and_item_lifecycle_events_keep_parent_identity(self) -> None:
        """Event and item changes use the parent FinanceEvent as aggregate."""
        event = FinanceService(self.session).create_event(
            self.user_id,
            FinanceEventCreate(
                operation_type="expense",
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
        self.finance_ids.append(event.id)
        item = event.items[0]

        FinanceService(self.session).update_event(
            self.user_id,
            event.id,
            FinanceEventUpdate(place="market"),
        )
        event_update = self._track_outbox_for(
            "finance_event", event.id, "finance_event.updated"
        )
        self.assertEqual(event_update.user_id, self.user_id)
        self.assertEqual(event_update.aggregate_id, event.id)

        FinanceService(self.session).update_item(
            self.user_id,
            event.id,
            item.id,
            FinanceItemUpdate(total_price=Decimal("200.00")),
        )
        item_update = self._track_outbox_for(
            "finance_event", event.id, "finance_event.updated"
        )
        self.assertEqual(item_update.user_id, self.user_id)
        self.assertEqual(item_update.aggregate_id, event.id)

        FinanceService(self.session).delete_item(self.user_id, event.id, item.id)
        item_delete = self._track_outbox_for(
            "finance_event", event.id, "finance_event.item_deleted"
        )
        self.assertEqual(item_delete.user_id, self.user_id)
        self.assertEqual(item_delete.aggregate_id, event.id)

        self.assertTrue(FinanceService(self.session).delete_event(self.user_id, event.id))
        event_delete = self._track_outbox_for(
            "finance_event", event.id, "finance_event.deleted"
        )
        self.assertEqual(event_delete.user_id, self.user_id)

    def test_outbox_rolls_back_when_event_creation_fails(self) -> None:
        """A failure before commit persists neither Memory nor NotificationOutbox."""
        with patch.object(
            NotificationOutboxService,
            "enqueue_created",
            side_effect=RuntimeError("outbox failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "outbox failure"):
                MemoryService(self.session).create_memory(self.user_id, "must rollback", "test")

        self.assertFalse(
            any(
                memory.text == "must rollback"
                for memory in MemoryService(self.session).list_memories(self.user_id)
            )
        )

    def test_dispatcher_sends_and_marks_event_sent(self) -> None:
        """The dispatcher delivers a pending event exactly once in normal operation."""
        memory = MemoryService(self.session).create_memory(self.user_id, "private", "test")
        self.memory_ids.append(memory.id)
        event = self._track_outbox_for("memory", memory.id)
        self.session.add(TelegramNotificationSubscription(user_id=self.user_id, chat_id=12345))
        self.session.commit()

        bot = FakeTelegramBot()
        event.created_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.session.commit()
        self.assertTrue(asyncio.run(NotificationDispatcher(bot).run_once()))
        self.session.expire_all()
        stored = self.session.get(NotificationOutbox, event.event_uuid)

        assert stored is not None
        self.assertEqual(stored.status, "sent")
        self.assertEqual(len(bot.messages), 1)
        self.assertNotIn("private", bot.messages[0][1])

    def test_dispatcher_retries_transient_error_and_disables_blocked_user(self) -> None:
        """Transient errors retry while a bot block disables the subscription."""
        memory = MemoryService(self.session).create_memory(self.user_id, "private", "test")
        self.memory_ids.append(memory.id)
        event = self._track_outbox_for("memory", memory.id)
        self.session.add(TelegramNotificationSubscription(user_id=self.user_id, chat_id=12345))
        self.session.commit()

        retry_bot = FakeTelegramBot(TelegramBotApiError(0, "network unavailable"))
        event.created_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
        self.session.commit()
        asyncio.run(NotificationDispatcher(retry_bot).run_once())
        self.session.expire_all()
        retried = self.session.get(NotificationOutbox, event.event_uuid)
        assert retried is not None
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.attempts, 1)
        self.assertGreater(retried.next_retry_at, datetime.now(timezone.utc))

        retried.next_retry_at = datetime.now(timezone.utc)
        self.session.commit()
        blocked_bot = FakeTelegramBot(TelegramBotApiError(403, "bot was blocked"))
        asyncio.run(NotificationDispatcher(blocked_bot).run_once())
        self.session.expire_all()
        failed = self.session.get(NotificationOutbox, event.event_uuid)
        subscription = self.session.get(TelegramNotificationSubscription, self.user_id)
        assert failed is not None and subscription is not None
        self.assertEqual(failed.status, "failed")
        self.assertFalse(subscription.is_enabled)

    def test_webhook_start_stop_and_unknown_identity(self) -> None:
        """Webhook commands activate, deactivate, and reject unknown identities safely."""
        settings = get_settings().model_copy(
            update={
                "telegram_webhook_secret": SecretStr("test-webhook-secret"),
                "telegram_bot_token": SecretStr("test-bot-token"),
            }
        )
        sent: list[str] = []

        class WebhookBot:
            """Captures webhook command responses without external I/O."""

            def __init__(self, _settings) -> None:
                pass

            async def send_message(self, _chat_id: int, text: str) -> None:
                sent.append(text)

        with patch("app.core.telegram_webhook_routes.get_settings", return_value=settings), patch(
            "app.core.telegram_webhook_routes.TelegramBotService", WebhookBot
        ):
            response = asyncio.run(self._call_webhook("/start", 6944966420, 12345))
            self.assertEqual(response.status_code, 200)
            self.session.expire_all()
            subscription = self.session.get(TelegramNotificationSubscription, self.user_id)
            assert subscription is not None
            self.assertTrue(subscription.is_enabled)

            asyncio.run(self._call_webhook("/stop", 6944966420, 12345))
            self.session.expire_all()
            assert subscription is not None
            self.assertFalse(subscription.is_enabled)

            asyncio.run(self._call_webhook("/start", 999999999, 98765))
        self.assertTrue(any("Сначала авторизуйтесь" in text for text in sent))

    @staticmethod
    async def _call_webhook(text: str, telegram_id: int, chat_id: int):
        """Construct one signed Telegram webhook request for direct route testing."""
        import json

        body = json.dumps(
            {
                "message": {
                    "text": text,
                    "from": {"id": telegram_id},
                    "chat": {"id": chat_id, "type": "private"},
                }
            }
        ).encode()
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/telegram/webhook",
                "headers": [(b"x-telegram-bot-api-secret-token", b"test-webhook-secret")],
            },
            receive,
        )
        return await telegram_webhook(request)
