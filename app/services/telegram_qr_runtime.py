"""In-process runtime for official Telethon QRLogin.wait flows."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, TYPE_CHECKING
from uuid import UUID

from telethon.errors import SessionPasswordNeededError

from app.services.telegram_user_api import TelegramUserApi, TelegramUserApiError, translate_telegram_error

if TYPE_CHECKING:
    from app.services.telegram_user_auth import TelegramUserAuthService


logger = logging.getLogger(__name__)


@dataclass
class PreparedQrLogin:
    """Live Telethon QR state before the durable flow row is committed."""

    client: Any
    qr_login: Any
    url: str
    expires_at: datetime
    session: str


@dataclass
class QrRuntime:
    """A QRLogin and its waiter owned by one MCP process."""

    user_id: int
    flow_id: UUID
    client: Any
    qr_login: Any
    url: str
    qr_expires_at: datetime
    deadline: datetime
    task: asyncio.Task


class TelegramQrRuntimeManager:
    """Keep QRLogin alive while the user scans it, isolated per user and flow."""

    def __init__(self) -> None:
        self._runtimes: dict[tuple[int, UUID], QrRuntime] = {}

    async def prepare(self, api: TelegramUserApi) -> PreparedQrLogin:
        """Connect and create QRLogin without serializing its runtime object."""
        client = api.client()
        try:
            await client.connect()
            qr_login = await api.qr_login(client)
            return PreparedQrLogin(
                client=client,
                qr_login=qr_login,
                url=qr_login.url,
                expires_at=qr_login.expires,
                session=client.session.save(),
            )
        except Exception:
            await client.disconnect()
            raise

    async def register(
        self,
        service: "TelegramUserAuthService",
        user_id: int,
        flow_id: UUID,
        prepared: PreparedQrLogin,
        deadline: datetime,
    ) -> None:
        task = asyncio.create_task(
            self._wait(service, user_id, flow_id, prepared, deadline),
            name=f"telegram-qr-{user_id}-{flow_id}",
        )
        self._runtimes[(user_id, flow_id)] = QrRuntime(
            user_id=user_id,
            flow_id=flow_id,
            client=prepared.client,
            qr_login=prepared.qr_login,
            url=prepared.url,
            qr_expires_at=prepared.expires_at,
            deadline=deadline,
            task=task,
        )

    async def cancel_user(self, user_id: int) -> None:
        """Cancel every live QR task for a user before starting a new flow."""
        keys = [key for key in self._runtimes if key[0] == user_id]
        for key in keys:
            await self.cancel(*key)

    async def cancel(self, user_id: int, flow_id: UUID) -> None:
        runtime = self._runtimes.pop((user_id, flow_id), None)
        if runtime is None:
            return
        if not runtime.task.done() and runtime.task is not asyncio.current_task():
            runtime.task.cancel()
            try:
                await runtime.task
            except asyncio.CancelledError:
                pass
        if runtime.client.is_connected():
            await runtime.client.disconnect()
        logger.info("Telegram QR runtime cancelled: user_id=%s flow_id=%s", user_id, flow_id)

    def current(self, user_id: int, flow_id: UUID) -> QrRuntime | None:
        return self._runtimes.get((user_id, flow_id))

    async def _wait(
        self,
        service: "TelegramUserAuthService",
        user_id: int,
        flow_id: UUID,
        prepared: PreparedQrLogin,
        deadline: datetime,
    ) -> None:
        key = (user_id, flow_id)
        runtime = self._runtimes.get(key)
        try:
            while datetime.now(timezone.utc) < deadline:
                remaining = max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
                qr_remaining = max(0.0, (runtime.qr_expires_at - datetime.now(timezone.utc)).total_seconds()) if runtime else 0.0
                if qr_remaining <= 0:
                    await prepared.qr_login.recreate()
                    self._update_runtime_qr(runtime, prepared.qr_login)
                    service._update_qr_metadata(flow_id, prepared.qr_login.expires)
                    continue
                try:
                    await asyncio.wait_for(prepared.qr_login.wait(), timeout=min(remaining, qr_remaining))
                    await service._finish(user_id, flow_id, prepared.client)
                    logger.info("Telegram QR runtime completed: user_id=%s flow_id=%s", user_id, flow_id)
                    return
                except asyncio.TimeoutError:
                    if datetime.now(timezone.utc) >= deadline:
                        break
                    await prepared.qr_login.recreate()
                    self._update_runtime_qr(runtime, prepared.qr_login)
                    service._update_qr_metadata(flow_id, prepared.qr_login.expires)
                    logger.info("Telegram QR runtime recreated: user_id=%s flow_id=%s", user_id, flow_id)
                except SessionPasswordNeededError:
                    service._update_flow(flow_id, "password_required", service._crypto.encrypt(prepared.client.session.save()))
                    logger.info("Telegram QR runtime requires 2FA: user_id=%s flow_id=%s", user_id, flow_id)
                    return
        except asyncio.CancelledError:
            raise
        except TelegramUserApiError as error:
            service._expire_flow(flow_id)
            logger.info("Telegram QR runtime failed: user_id=%s flow_id=%s rpc_error_class=%s", user_id, flow_id, error.__cause__.__class__.__name__ if error.__cause__ else error.__class__.__name__)
        except Exception as error:
            translate_telegram_error(error, "waiting for QR authorization")
            service._expire_flow(flow_id)
            logger.info("Telegram QR runtime failed: user_id=%s flow_id=%s rpc_error_class=%s", user_id, flow_id, error.__class__.__name__)
        finally:
            if key in self._runtimes:
                self._runtimes.pop(key, None)
            if prepared.client.is_connected():
                await prepared.client.disconnect()

        service._expire_flow(flow_id)

    @staticmethod
    def _update_runtime_qr(runtime: QrRuntime | None, qr_login: Any) -> None:
        if runtime is not None:
            runtime.url = qr_login.url
            runtime.qr_expires_at = qr_login.expires


telegram_qr_runtime = TelegramQrRuntimeManager()
