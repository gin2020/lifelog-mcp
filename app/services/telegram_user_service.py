"""Business operations for the authenticated Telegram user account."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.telegram_user import (
    TelegramAccount,
    TelegramAllowedPeer,
    TelegramDialogueMonitor,
    TelegramSendRequest,
)
from app.services.telegram_session_crypto import TelegramSessionCrypto
from app.services.telegram_user_api import TelegramPeer, TelegramUserApi, TelegramUserApiError


class TelegramUserServiceError(RuntimeError):
    """Safe business error for Telegram MCP tools."""


class TelegramUserService:
    """Enforce account ownership and allowlist checks around MTProto calls."""

    def __init__(self, settings: Settings | None = None, api: TelegramUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or TelegramUserApi(self._settings)
        self._crypto = TelegramSessionCrypto(self._settings)

    def _account(self, user_id: int) -> TelegramAccount:
        with SessionLocal() as db:
            account = db.scalar(select(TelegramAccount).where(TelegramAccount.user_id == user_id))
            if account is None or account.status != "active" or not account.session_ciphertext:
                raise TelegramUserServiceError("Telegram User API is not connected")
            db.expunge(account)
            return account

    async def _client(self, user_id: int):
        account = self._account(user_id)
        client = self._api.client(self._crypto.decrypt(account.session_ciphertext))
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramUserServiceError("Telegram session is no longer authorized")
            yield client
        finally:
            await client.disconnect()

    async def search_contacts(self, user_id: int, query: str) -> dict[str, object]:
        query = query.strip()
        if not query:
            raise TelegramUserServiceError("Telegram contact search query is required")
        async for client in self._client(user_id):
            try:
                peers = await self._api.search_contacts(client, query)
            except TelegramUserApiError as error:
                raise TelegramUserServiceError(str(error)) from error
        return {"results": [self._search_result(peer) for peer in peers]}

    async def add_allowed_peer(self, user_id: int, reference: str | int) -> dict[str, object]:
        if isinstance(reference, int):
            resolved_reference: str | int = reference
        else:
            resolved_reference = reference.strip()
        if not resolved_reference or (
            isinstance(resolved_reference, str)
            and not resolved_reference.lstrip("-").isdigit()
            and not resolved_reference.startswith("@")
        ):
            raise TelegramUserServiceError("Use an exact Telegram ID or @username; names require explicit resolution")
        async for client in self._client(user_id):
            try:
                peer = await self._api.resolve_peer(
                    client,
                    int(resolved_reference)
                    if isinstance(resolved_reference, str) and resolved_reference.lstrip("-").isdigit()
                    else resolved_reference,
                )
            except TelegramUserApiError as error:
                raise TelegramUserServiceError(str(error)) from error
        with SessionLocal() as db:
            allowed = db.scalar(select(TelegramAllowedPeer).where(
                TelegramAllowedPeer.user_id == user_id,
                TelegramAllowedPeer.peer_type == peer.peer_type,
                TelegramAllowedPeer.telegram_peer_id == peer.telegram_peer_id,
            ))
            if allowed is None:
                allowed = TelegramAllowedPeer(user_id=user_id, peer_type=peer.peer_type, telegram_peer_id=peer.telegram_peer_id)
                db.add(allowed)
            allowed.access_hash = peer.access_hash
            allowed.username = peer.username
            allowed.display_name = peer.display_name
            allowed.is_enabled = True
            db.commit()
            return self._peer_result(allowed)

    def list_allowed_peers(self, user_id: int) -> list[dict[str, object]]:
        with SessionLocal() as db:
            return [self._peer_result(peer) for peer in db.scalars(select(TelegramAllowedPeer).where(TelegramAllowedPeer.user_id == user_id).order_by(TelegramAllowedPeer.id))]

    def remove_allowed_peer(self, user_id: int, allowed_peer_id: int) -> bool:
        with SessionLocal() as db:
            peer = db.scalar(select(TelegramAllowedPeer).where(TelegramAllowedPeer.id == allowed_peer_id, TelegramAllowedPeer.user_id == user_id))
            if peer is None:
                return False
            peer.is_enabled = False
            db.commit()
            return True

    def _allowed_peer(self, db, user_id: int, allowed_peer_id: int) -> TelegramAllowedPeer:
        peer = db.scalar(select(TelegramAllowedPeer).where(TelegramAllowedPeer.id == allowed_peer_id, TelegramAllowedPeer.user_id == user_id, TelegramAllowedPeer.is_enabled.is_(True)))
        if peer is None:
            raise TelegramUserServiceError("Telegram peer is not in the allowlist")
        return peer

    def create_send_request(self, user_id: int, allowed_peer_id: int, text: str) -> dict[str, object]:
        if not text.strip():
            raise TelegramUserServiceError("Message text is required")
        with SessionLocal() as db:
            peer = self._allowed_peer(db, user_id, allowed_peer_id)
            request = TelegramSendRequest(
                id=uuid4(), user_id=user_id, allowed_peer_id=peer.id,
                telegram_peer_id=peer.telegram_peer_id,
                message_ciphertext=self._crypto.encrypt(text),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._settings.telegram_send_confirmation_ttl_seconds),
            )
            db.add(request)
            db.commit()
            return {"request_id": str(request.id), "status": "confirmation_required", "recipient": self._peer_result(peer), "text": text}

    async def confirm_send(self, user_id: int, request_id: UUID) -> dict[str, object]:
        with SessionLocal() as db:
            request = db.scalar(select(TelegramSendRequest).where(TelegramSendRequest.id == request_id, TelegramSendRequest.user_id == user_id).with_for_update())
            if request is None or request.status != "pending" or request.expires_at <= datetime.now(timezone.utc):
                if request is not None and request.status == "pending" and request.expires_at <= datetime.now(timezone.utc):
                    request.status = "expired"
                    request.message_ciphertext = ""
                    db.commit()
                raise TelegramUserServiceError("Send request is invalid, expired, or already used")
            peer = self._allowed_peer(db, user_id, request.allowed_peer_id)
            text = self._crypto.decrypt(request.message_ciphertext)
            request.status = "sending"
            db.commit()
        try:
            async for client in self._client(user_id):
                message_id = await self._api.send_message(client, request.telegram_peer_id, text)
        except Exception as error:
            with SessionLocal() as db:
                stored = db.get(TelegramSendRequest, request_id)
                if stored is not None:
                    stored.status = "failed"
                    stored.error = "Telegram send failed"
                    db.commit()
            if isinstance(error, TelegramUserServiceError):
                raise
            raise TelegramUserServiceError("Telegram message could not be sent") from error
        with SessionLocal() as db:
            stored = db.get(TelegramSendRequest, request_id)
            stored.status = "sent"
            stored.telegram_message_id = message_id
            stored.message_ciphertext = ""
            db.commit()
        return {"request_id": str(request_id), "status": "sent", "allowed_peer_id": peer.id, "telegram_message_id": message_id}

    def cancel_send(self, user_id: int, request_id: UUID) -> dict[str, object]:
        """Invalidate a pending send without invoking Telegram."""
        with SessionLocal() as db:
            request = db.scalar(select(TelegramSendRequest).where(
                TelegramSendRequest.id == request_id,
                TelegramSendRequest.user_id == user_id,
            ).with_for_update())
            if request is None or request.status != "pending":
                raise TelegramUserServiceError("Send request is invalid, expired, or already used")
            if request.expires_at <= datetime.now(timezone.utc):
                request.status = "expired"
                request.message_ciphertext = ""
                db.commit()
                raise TelegramUserServiceError("Send request is invalid, expired, or already used")
            request.status = "cancelled"
            request.message_ciphertext = ""
            db.commit()
            return {"request_id": str(request_id), "status": "cancelled"}

    async def get_messages(self, user_id: int, allowed_peer_id: int, limit: int, after_message_id: int | None = None) -> list[dict[str, object]]:
        limit = min(max(limit, 1), self._settings.telegram_max_messages_per_request)
        with SessionLocal() as db:
            peer = self._allowed_peer(db, user_id, allowed_peer_id)
            peer_id = peer.telegram_peer_id
        async for client in self._client(user_id):
            try:
                return await self._api.get_messages(client, peer_id, limit, after_message_id)
            except TelegramUserApiError as error:
                raise TelegramUserServiceError(str(error)) from error

    async def get_new_messages(self, user_id: int, limit: int) -> list[dict[str, object]]:
        limit = min(max(limit, 1), self._settings.telegram_max_messages_per_request)
        with SessionLocal() as db:
            monitor_data = [(monitor, peer.id, peer.telegram_peer_id) for monitor, peer in db.execute(
                select(TelegramDialogueMonitor, TelegramAllowedPeer)
                .join(TelegramAllowedPeer, TelegramAllowedPeer.id == TelegramDialogueMonitor.allowed_peer_id)
                .where(
                    TelegramDialogueMonitor.user_id == user_id,
                    TelegramDialogueMonitor.is_active.is_(True),
                    TelegramAllowedPeer.is_enabled.is_(True),
                )
            )]
        result: list[dict[str, object]] = []
        async for client in self._client(user_id):
            for monitor, allowed_peer_id, telegram_peer_id in monitor_data:
                messages = await self._api.get_messages(client, telegram_peer_id, limit, monitor.last_read_message_id)
                messages = [message for message in messages if not message.get("out")]
                for message in messages:
                    result.append({"allowed_peer_id": allowed_peer_id, **message})
                if messages:
                    with SessionLocal() as db:
                        stored = db.get(TelegramDialogueMonitor, monitor.id)
                        if stored is not None:
                            stored.last_read_message_id = max(message["id"] for message in messages if isinstance(message.get("id"), int))
                            db.commit()
        return result

    def start_monitoring(self, user_id: int, allowed_peer_id: int, kind: str, anchor_message_id: int | None = None) -> dict[str, object]:
        if kind not in {"new_messages", "reply"}:
            raise TelegramUserServiceError("Monitor kind must be new_messages or reply")
        with SessionLocal() as db:
            self._allowed_peer(db, user_id, allowed_peer_id)
            monitor = db.scalar(select(TelegramDialogueMonitor).where(TelegramDialogueMonitor.user_id == user_id, TelegramDialogueMonitor.allowed_peer_id == allowed_peer_id))
            if monitor is None:
                monitor = TelegramDialogueMonitor(user_id=user_id, allowed_peer_id=allowed_peer_id)
                db.add(monitor)
            monitor.monitor_kind = kind
            monitor.anchor_message_id = anchor_message_id
            monitor.is_active = True
            db.commit()
            return {"monitor_id": monitor.id, "allowed_peer_id": allowed_peer_id, "status": "active", "kind": kind}

    def stop_monitoring(self, user_id: int, allowed_peer_id: int) -> bool:
        with SessionLocal() as db:
            monitor = db.scalar(select(TelegramDialogueMonitor).where(TelegramDialogueMonitor.user_id == user_id, TelegramDialogueMonitor.allowed_peer_id == allowed_peer_id))
            if monitor is None:
                return False
            monitor.is_active = False
            db.commit()
            return True

    @staticmethod
    def _peer_result(peer: TelegramAllowedPeer) -> dict[str, object]:
        return {"allowed_peer_id": peer.id, "peer_type": peer.peer_type, "telegram_peer_id": peer.telegram_peer_id, "username": peer.username, "display_name": peer.display_name, "is_enabled": peer.is_enabled}

    @staticmethod
    def _search_result(peer: TelegramPeer) -> dict[str, object]:
        return {
            "peer_type": peer.peer_type,
            "telegram_peer_id": peer.telegram_peer_id,
            "username": peer.username,
            "display_name": peer.display_name,
            "phone": peer.phone,
            "is_contact": peer.is_contact,
        }
