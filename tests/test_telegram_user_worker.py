"""Regression tests for independent Telegram read and notification cursors."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from app.config.settings import Settings
from app.db.database import SessionLocal
from app.db.models.notification import NotificationOutbox
from app.db.models.telegram_user import (
    TelegramAccount,
    TelegramAllowedPeer,
    TelegramDialogueMonitor,
)
from app.db.models.user import User
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramUserApiError
from app.services.telegram_user_service import TelegramUserService
from app.services.telegram_user_worker import TelegramUserWorker


TEST_SESSION_KEY = Fernet.generate_key().decode()


class FakeTelegramClient:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True


class FakeTelegramApi:
    def __init__(self, messages_by_peer: dict[int, list[dict[str, object]]], errors: set[int] | None = None):
        self.messages_by_peer = messages_by_peer
        self.errors = errors or set()
        self.calls: list[tuple[int, int | None]] = []
        self.client_instance = FakeTelegramClient()

    def client(self, _session: str) -> FakeTelegramClient:
        return self.client_instance

    async def get_messages(
        self,
        _client: FakeTelegramClient,
        peer: int,
        _limit: int,
        min_id: int | None = None,
    ) -> list[dict[str, object]]:
        self.calls.append((peer, min_id))
        if peer in self.errors:
            raise TelegramUserApiError("Telegram messages could not be loaded")
        return [
            message
            for message in self.messages_by_peer.get(peer, [])
            if isinstance(message.get("id"), int) and message["id"] > (min_id or 0)
        ]


def make_settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        default_user_telegram_id=6944966420,
        telegram_api_id=123,
        telegram_api_hash=SecretStr("api-hash"),
        telegram_session_encryption_key=SecretStr(TEST_SESSION_KEY),
        telegram_max_messages_per_request=20,
    )


def create_monitor(*, peer_id: int, anchor_message_id: int | None = None):
    settings = make_settings()
    crypto = TelegramSessionCrypto(settings)
    with SessionLocal() as db:
        user = User()
        db.add(user)
        db.flush()
        account = TelegramAccount(
            user_id=user.id,
            status="active",
            session_ciphertext=crypto.encrypt("session"),
        )
        peer = TelegramAllowedPeer(
            user_id=user.id,
            peer_type="user",
            telegram_peer_id=peer_id,
            display_name=f"Peer {peer_id}",
        )
        db.add_all([account, peer])
        db.flush()
        monitor = TelegramDialogueMonitor(
            user_id=user.id,
            allowed_peer_id=peer.id,
            anchor_message_id=anchor_message_id,
            updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        db.add(monitor)
        db.commit()
        return settings, user.id, monitor.id


def load_monitor(monitor_id: int) -> TelegramDialogueMonitor:
    with SessionLocal() as db:
        monitor = db.get(TelegramDialogueMonitor, monitor_id)
        assert monitor is not None
        return SimpleNamespace(
            id=monitor.id,
            last_read_message_id=monitor.last_read_message_id,
            last_notified_message_id=monitor.last_notified_message_id,
            updated_at=monitor.updated_at,
        )


def load_events(monitor_id: int) -> list[NotificationOutbox]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.aggregate_type == "telegram_dialogue_monitor",
                    NotificationOutbox.aggregate_id == monitor_id,
                    NotificationOutbox.event_type == "telegram.new_messages",
                )
                .order_by(NotificationOutbox.created_at)
            )
        )


def test_manual_read_does_not_advance_worker_notification_cursor() -> None:
    """Reproduce production: manual read first, then worker still notifies."""
    settings, user_id, monitor_id = create_monitor(peer_id=200)
    api = FakeTelegramApi({200: [{"id": 13454, "out": False, "text": "hello"}]})

    messages = asyncio.run(TelegramUserService(settings, api=api).get_new_messages(user_id, 20))
    assert [message["id"] for message in messages] == [13454]
    assert load_monitor(monitor_id).last_read_message_id == 13454
    assert load_monitor(monitor_id).last_notified_message_id is None

    assert asyncio.run(TelegramUserWorker(settings, api=api).run_once()) is True

    monitor = load_monitor(monitor_id)
    assert monitor.last_read_message_id == 13454
    assert monitor.last_notified_message_id == 13454
    events = load_events(monitor_id)
    assert len(events) == 1
    assert events[0].payload["last_message_id"] == 13454


def test_worker_rerun_is_idempotent_for_same_message() -> None:
    settings, user_id, monitor_id = create_monitor(peer_id=201)
    api = FakeTelegramApi({201: [{"id": 300, "out": False}]})
    worker = TelegramUserWorker(settings, api=api)

    assert asyncio.run(worker.run_once()) is True
    assert asyncio.run(worker.run_once()) is True
    assert TelegramUserWorker._advance_monitor(user_id, monitor_id, 300, 1) is False

    assert len(load_events(monitor_id)) == 1
    assert load_monitor(monitor_id).last_notified_message_id == 300


def test_manual_read_after_worker_does_not_change_notification_cursor() -> None:
    settings, user_id, monitor_id = create_monitor(peer_id=206)
    api = FakeTelegramApi({206: [{"id": 600, "out": False}]})
    worker = TelegramUserWorker(settings, api=api)

    assert asyncio.run(worker.run_once()) is True
    messages = asyncio.run(TelegramUserService(settings, api=api).get_new_messages(user_id, 20))

    assert [message["id"] for message in messages] == [600]
    monitor = load_monitor(monitor_id)
    assert monitor.last_read_message_id == 600
    assert monitor.last_notified_message_id == 600
    assert len(load_events(monitor_id)) == 1


def test_worker_processes_multiple_monitors_fairly_without_messages() -> None:
    settings, user_id, first_monitor_id = create_monitor(peer_id=202)
    with SessionLocal() as db:
        first = db.get(TelegramDialogueMonitor, first_monitor_id)
        assert first is not None
        peer = TelegramAllowedPeer(user_id=user_id, peer_type="user", telegram_peer_id=203)
        db.add(peer)
        db.flush()
        db.add(
            TelegramDialogueMonitor(
                user_id=user_id,
                allowed_peer_id=peer.id,
                updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()

    api = FakeTelegramApi({202: [], 203: []})
    worker = TelegramUserWorker(settings, api=api)
    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())

    assert [peer_id for peer_id, _ in api.calls] == [202, 203]
    assert load_events(first_monitor_id) == []


def test_monitor_error_does_not_starve_other_monitors() -> None:
    settings, user_id, first_monitor_id = create_monitor(peer_id=204)
    with SessionLocal() as db:
        peer = TelegramAllowedPeer(user_id=user_id, peer_type="user", telegram_peer_id=205)
        db.add(peer)
        db.flush()
        db.add(
            TelegramDialogueMonitor(
                user_id=user_id,
                allowed_peer_id=peer.id,
                updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.commit()

    api = FakeTelegramApi(
        {205: [{"id": 500, "out": False}]},
        errors={204},
    )
    worker = TelegramUserWorker(settings, api=api)
    asyncio.run(worker.run_once())
    asyncio.run(worker.run_once())

    assert [peer_id for peer_id, _ in api.calls] == [204, 205]
    assert len(load_events(first_monitor_id)) == 0
