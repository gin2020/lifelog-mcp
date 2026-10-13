"""Business operations around authenticated MAX personal accounts."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.max_user import MaxAccount, MaxAllowedChat, MaxChatMonitor, MaxSendRequest
from app.services.max_session_crypto import MaxSessionCrypto
from app.services.max_user_api import MaxChat, MaxClientHandle, MaxUserApi, MaxUserApiError


class MaxUserServiceError(RuntimeError):
    """Safe business error for MAX MCP tools."""


class MaxUserService:
    def __init__(self, settings: Settings | None = None, api: MaxUserApi | None = None) -> None:
        self._settings = settings or get_settings()
        self._api = api or MaxUserApi(self._settings)
        self._crypto = MaxSessionCrypto(self._settings)

    def _account(self, user_id: int, account_id: int | None = None) -> MaxAccount:
        with SessionLocal() as db:
            query = select(MaxAccount).where(MaxAccount.user_id == user_id)
            if account_id is not None:
                query = query.where(MaxAccount.id == account_id)
            accounts = list(db.scalars(query.order_by(MaxAccount.id)))
            if account_id is None and len(accounts) > 1:
                raise MaxUserServiceError("Multiple MAX accounts are connected; account_id is required")
            account = accounts[0] if accounts else None
            if account is None or account.status != "active" or not account.session_ciphertext:
                raise MaxUserServiceError("MAX User API is not connected")
            db.expunge(account)
            return account

    @asynccontextmanager
    async def _client(self, user_id: int, account_id: int | None = None) -> AsyncIterator[tuple[MaxAccount, MaxClientHandle]]:
        account = self._account(user_id, account_id)
        handle = self._api.create_client(
            self._crypto.decrypt(account.session_ciphertext or ""),
            device_id=account.device_id,
        )
        try:
            await self._api.connect(handle)
            yield account, handle
        except MaxUserApiError as error:
            self._mark_error(user_id, account.id, str(error))
            raise MaxUserServiceError(str(error)) from error
        finally:
            session_blob = handle.read_session()
            await handle.close()
            if session_blob:
                self._save_session(user_id, account.id, session_blob)

    async def list_chats(self, user_id: int, account_id: int | None = None, query: str | None = None) -> list[dict[str, object]]:
        async with self._client(user_id, account_id) as (account, handle):
            chats = await self._api.get_chats(handle.client)
        folded = query.strip().casefold() if query else None
        return [
            {
                "account_id": account.id,
                "account_label": account.label,
                "max_chat_id": chat.max_chat_id,
                "display_name": chat.display_name,
                "chat_type": chat.chat_type,
                "max_user_id": chat.max_user_id,
                "username": chat.username,
                "phone": chat.phone,
                "profile_link": chat.profile_link,
                "participant_ids": list(chat.participant_ids),
                "contact_found": chat.contact_found,
            }
            for chat in chats
            if folded is None or self._chat_matches(chat, folded)
        ]

    async def add_allowed_chat(self, user_id: int, max_chat_id: int, account_id: int | None = None) -> dict[str, object]:
        async with self._client(user_id, account_id) as (account, handle):
            chats = await self._api.get_chats(handle.client)
        chat = next((item for item in chats if item.max_chat_id == max_chat_id), None)
        if chat is None:
            raise MaxUserServiceError("MAX chat was not found in the connected account")
        with SessionLocal() as db:
            allowed = db.scalar(select(MaxAllowedChat).where(MaxAllowedChat.user_id == user_id, MaxAllowedChat.account_id == account.id, MaxAllowedChat.max_chat_id == max_chat_id))
            if allowed is None:
                allowed = MaxAllowedChat(user_id=user_id, account_id=account.id, max_chat_id=chat.max_chat_id)
                db.add(allowed)
            allowed.display_name = chat.display_name
            allowed.chat_type = chat.chat_type
            allowed.is_enabled = True
            db.commit()
            return {
                **self._allowed_result(allowed, account),
                "max_user_id": chat.max_user_id,
                "username": chat.username,
                "phone": chat.phone,
                "profile_link": chat.profile_link,
                "participant_ids": list(chat.participant_ids),
                "contact_found": chat.contact_found,
            }

    def list_allowed_chats(self, user_id: int, account_id: int | None = None) -> list[dict[str, object]]:
        with SessionLocal() as db:
            query = select(MaxAllowedChat, MaxAccount).join(MaxAccount, MaxAccount.id == MaxAllowedChat.account_id).where(MaxAllowedChat.user_id == user_id)
            if account_id is not None:
                query = query.where(MaxAllowedChat.account_id == account_id)
            return [self._allowed_result(chat, account) for chat, account in db.execute(query.order_by(MaxAllowedChat.id))]

    def remove_allowed_chat(self, user_id: int, allowed_chat_id: int) -> bool:
        with SessionLocal() as db:
            chat = db.scalar(select(MaxAllowedChat).where(MaxAllowedChat.id == allowed_chat_id, MaxAllowedChat.user_id == user_id))
            if chat is None:
                return False
            chat.is_enabled = False
            db.commit()
            return True

    async def get_messages(self, user_id: int, allowed_chat_id: int, limit: int = 20, after_message_id: int | None = None, account_id: int | None = None) -> list[dict[str, object]]:
        limit = min(max(limit, 1), self._settings.max_max_messages_per_request)
        allowed, account = self._allowed_chat(user_id, allowed_chat_id, account_id)
        async with self._client(user_id, account.id) as (_, handle):
            messages = await self._api.get_history(handle.client, allowed.max_chat_id, limit)
        if after_message_id is not None:
            messages = [message for message in messages if isinstance(message.get("id"), int) and message["id"] > after_message_id]
        return [self._message_result(message, allowed, account) for message in messages]

    async def get_new_messages(self, user_id: int, limit: int = 20, account_id: int | None = None) -> list[dict[str, object]]:
        limit = min(max(limit, 1), self._settings.max_max_messages_per_request)
        with SessionLocal() as db:
            query = select(MaxChatMonitor, MaxAllowedChat, MaxAccount).join(MaxAllowedChat, MaxAllowedChat.id == MaxChatMonitor.allowed_chat_id).join(MaxAccount, MaxAccount.id == MaxChatMonitor.account_id).where(MaxChatMonitor.user_id == user_id, MaxChatMonitor.is_active.is_(True), MaxAllowedChat.is_enabled.is_(True))
            if account_id is not None:
                query = query.where(MaxChatMonitor.account_id == account_id)
            monitors = list(db.execute(query))
        result: list[dict[str, object]] = []
        for monitor, allowed, account in monitors:
            async with self._client(user_id, account.id) as (_, handle):
                messages = await self._api.get_history(handle.client, allowed.max_chat_id, limit)
            new_messages = [message for message in messages if not self._is_outgoing(message, account.max_user_id) and isinstance(message.get("id"), int) and (monitor.last_read_message_id is None or message["id"] > monitor.last_read_message_id)]
            result.extend(self._message_result(message, allowed, account) for message in new_messages)
            if new_messages:
                with SessionLocal() as db:
                    stored = db.get(MaxChatMonitor, monitor.id)
                    if stored is not None:
                        stored.last_read_message_id = max(message["id"] for message in new_messages)
                        db.commit()
        return result

    def create_send_request(self, user_id: int, allowed_chat_id: int, text: str, account_id: int | None = None) -> dict[str, object]:
        if not text.strip():
            raise MaxUserServiceError("Message text is required")
        allowed, account = self._allowed_chat(user_id, allowed_chat_id, account_id)
        with SessionLocal() as db:
            request = MaxSendRequest(
                id=uuid4(), user_id=user_id, allowed_chat_id=allowed.id, account_id=account.id,
                max_chat_id=allowed.max_chat_id, message_ciphertext=self._crypto.encrypt(text),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._settings.max_send_confirmation_ttl_seconds),
            )
            db.add(request)
            db.commit()
            return {"request_id": str(request.id), "status": "confirmation_required", "account_id": account.id, "account_label": account.label, "recipient": self._allowed_result(allowed, account), "text": text}

    async def confirm_send(self, user_id: int, request_id: UUID) -> dict[str, object]:
        with SessionLocal() as db:
            request = db.scalar(select(MaxSendRequest).where(MaxSendRequest.id == request_id, MaxSendRequest.user_id == user_id).with_for_update())
            if request is None or request.status != "pending" or request.expires_at <= datetime.now(timezone.utc):
                if request is not None and request.status == "pending":
                    request.status = "expired"
                    request.message_ciphertext = ""
                    db.commit()
                raise MaxUserServiceError("Send request is invalid, expired, or already used")
            text = self._crypto.decrypt(request.message_ciphertext).decode("utf-8")
            request.status = "sending"
            db.commit()
            account_id = request.account_id
            chat_id = request.max_chat_id
        try:
            async with self._client(user_id, account_id) as (_, handle):
                message_id = await self._api.send_message(handle.client, chat_id, text)
        except Exception as error:
            with SessionLocal() as db:
                stored = db.get(MaxSendRequest, request_id)
                if stored is not None:
                    stored.status = "failed"
                    stored.error = "MAX send failed"
                    stored.message_ciphertext = ""
                    db.commit()
            if isinstance(error, MaxUserServiceError):
                raise
            raise MaxUserServiceError("MAX message could not be sent") from error
        with SessionLocal() as db:
            stored = db.get(MaxSendRequest, request_id)
            if stored is not None:
                stored.status = "sent"
                stored.max_message_id = message_id
                stored.message_ciphertext = ""
                db.commit()
        return {"request_id": str(request_id), "status": "sent", "account_id": account_id, "max_chat_id": chat_id, "max_message_id": message_id}

    def cancel_send(self, user_id: int, request_id: UUID) -> dict[str, object]:
        with SessionLocal() as db:
            request = db.scalar(select(MaxSendRequest).where(MaxSendRequest.id == request_id, MaxSendRequest.user_id == user_id).with_for_update())
            if request is None or request.status != "pending":
                raise MaxUserServiceError("Send request is invalid, expired, or already used")
            request.status = "cancelled"
            request.message_ciphertext = ""
            db.commit()
            return {"request_id": str(request_id), "status": "cancelled", "account_id": request.account_id}

    def start_monitoring(self, user_id: int, allowed_chat_id: int, kind: str = "new_messages", anchor_message_id: int | None = None, account_id: int | None = None) -> dict[str, object]:
        if kind not in {"new_messages", "reply"}:
            raise MaxUserServiceError("Monitor kind must be new_messages or reply")
        allowed, account = self._allowed_chat(user_id, allowed_chat_id, account_id)
        with SessionLocal() as db:
            monitor = db.scalar(select(MaxChatMonitor).where(MaxChatMonitor.user_id == user_id, MaxChatMonitor.allowed_chat_id == allowed.id, MaxChatMonitor.account_id == account.id))
            created = monitor is None
            if monitor is None:
                monitor = MaxChatMonitor(user_id=user_id, account_id=account.id, allowed_chat_id=allowed.id)
                db.add(monitor)
            monitor.monitor_kind = kind
            monitor.anchor_message_id = anchor_message_id
            monitor.is_active = True
            db.commit()
            return {"monitor_id": monitor.id, "allowed_chat_id": allowed.id, "account_id": account.id, "account_label": account.label, "display_name": allowed.display_name, "status": "active", "kind": kind, "created": created}

    def stop_monitoring(self, user_id: int, allowed_chat_id: int, account_id: int | None = None) -> bool:
        with SessionLocal() as db:
            query = select(MaxChatMonitor).where(MaxChatMonitor.user_id == user_id, MaxChatMonitor.allowed_chat_id == allowed_chat_id)
            if account_id is not None:
                query = query.where(MaxChatMonitor.account_id == account_id)
            monitor = db.scalar(query)
            if monitor is None:
                return False
            monitor.is_active = False
            db.commit()
            return True

    def _allowed_chat(self, user_id: int, allowed_chat_id: int, account_id: int | None = None) -> tuple[MaxAllowedChat, MaxAccount]:
        with SessionLocal() as db:
            query = select(MaxAllowedChat, MaxAccount).join(MaxAccount, MaxAccount.id == MaxAllowedChat.account_id).where(MaxAllowedChat.id == allowed_chat_id, MaxAllowedChat.user_id == user_id, MaxAllowedChat.is_enabled.is_(True))
            if account_id is not None:
                query = query.where(MaxAllowedChat.account_id == account_id)
            row = db.execute(query).first()
            if row is None:
                raise MaxUserServiceError("MAX chat is not in the allowlist")
            allowed, account = row
            if account.status != "active" or not account.session_ciphertext:
                raise MaxUserServiceError("MAX account is not connected")
            db.expunge(allowed)
            db.expunge(account)
            return allowed, account

    @staticmethod
    def _allowed_result(chat: MaxAllowedChat, account: MaxAccount) -> dict[str, object]:
        return {"allowed_chat_id": chat.id, "account_id": account.id, "account_label": account.label, "max_chat_id": chat.max_chat_id, "display_name": chat.display_name, "chat_type": chat.chat_type, "is_enabled": chat.is_enabled}

    @staticmethod
    def _chat_matches(chat: MaxChat, query: str) -> bool:
        """Match a chat against every identity field returned by MAX."""
        normalized_query = query.lstrip("@").casefold()
        values = (
            chat.display_name,
            chat.username,
            chat.phone,
            chat.profile_link,
            chat.max_user_id,
            chat.max_chat_id,
            *chat.participant_ids,
        )
        text = " ".join(str(value).casefold() for value in values if value is not None)
        if normalized_query in text or query in text:
            return True

        # MAX returns phone as an integer in the current PyMax model.  Ignore
        # separators when a caller searches using a formatted phone number.
        query_digits = "".join(character for character in query if character.isdigit())
        phone_digits = "".join(character for character in (chat.phone or "") if character.isdigit())
        return bool(query_digits and phone_digits and query_digits in phone_digits)

    @staticmethod
    def _message_result(message: dict[str, object], chat: MaxAllowedChat, account: MaxAccount) -> dict[str, object]:
        return {"account_id": account.id, "account_label": account.label, "allowed_chat_id": chat.id, **message}

    @staticmethod
    def _is_outgoing(message: dict[str, object], max_user_id: int | None) -> bool:
        return bool(message.get("out")) or (max_user_id is not None and message.get("sender_id") == max_user_id)

    def _save_session(self, user_id: int, account_id: int, session_blob: bytes) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.id == account_id, MaxAccount.user_id == user_id))
            if account is not None:
                account.session_ciphertext = self._crypto.encrypt(session_blob)
                account.last_sync_at = datetime.now(timezone.utc)
                account.last_error = None
                db.commit()

    @staticmethod
    def _mark_error(user_id: int, account_id: int, error: str) -> None:
        with SessionLocal() as db:
            account = db.scalar(select(MaxAccount).where(MaxAccount.id == account_id, MaxAccount.user_id == user_id))
            if account is not None:
                account.last_error = error[:1000]
                db.commit()
