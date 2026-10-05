"""MCP tools for a user's Telegram account through MTProto."""

from typing import Any
from uuid import UUID

from app.core.dependencies import get_request_user_id
from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.services.telegram_user_auth import TelegramUserAuthError, TelegramUserAuthService
from app.services.telegram_user_service import TelegramUserService, TelegramUserServiceError


def _user_id() -> int:
    with SessionLocal() as db:
        return get_request_user_id(db)


@mcp.tool()
async def telegram_connect(operation: str, phone: str | None = None, flow_id: str | None = None, code: str | None = None, password: str | None = None) -> dict[str, object]:
    """Start or continue staged Telegram User API authorization."""
    service = TelegramUserAuthService()
    user_id = _user_id()
    try:
        if operation == "start":
            if phone is None:
                raise TelegramUserAuthError("phone is required for start")
            return await service.start(user_id, phone)
        if operation == "qr_start":
            return await service.qr_start(user_id)
        if flow_id is None:
            raise TelegramUserAuthError("flow_id is required")
        parsed = UUID(flow_id)
        if operation == "qr_status":
            return await service.qr_status(user_id, parsed)
        if operation == "cancel":
            return {"flow_id": flow_id, "status": "cancelled" if service.cancel(user_id, parsed) else "not_found"}
        if operation == "submit_code":
            if code is None:
                raise TelegramUserAuthError("code is required")
            return await service.submit_code(user_id, parsed, code)
        if operation == "submit_2fa":
            if password is None:
                raise TelegramUserAuthError("password is required")
            return await service.submit_2fa(user_id, parsed, password)
        raise TelegramUserAuthError("operation must be start, submit_code, or submit_2fa")
    except (TelegramUserAuthError, ValueError) as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def telegram_status() -> dict[str, object]:
    return TelegramUserAuthService().status(_user_id())


@mcp.tool()
def telegram_disconnect() -> dict[str, str]:
    return {"status": "disconnected" if TelegramUserAuthService().disconnect(_user_id()) else "not_connected"}


@mcp.tool()
async def telegram_add_allowed_contact(reference: str) -> dict[str, object]:
    return await TelegramUserService().add_allowed_peer(_user_id(), reference)


@mcp.tool()
def telegram_list_allowed_contacts() -> list[dict[str, object]]:
    return TelegramUserService().list_allowed_peers(_user_id())


@mcp.tool()
def telegram_remove_allowed_contact(allowed_peer_id: int) -> dict[str, object]:
    return {"allowed_peer_id": allowed_peer_id, "status": "removed" if TelegramUserService().remove_allowed_peer(_user_id(), allowed_peer_id) else "not_found"}


@mcp.tool()
def telegram_send_message(allowed_peer_id: int, text: str) -> dict[str, object]:
    return TelegramUserService().create_send_request(_user_id(), allowed_peer_id, text)


@mcp.tool()
async def telegram_confirm_send(request_id: str) -> dict[str, object]:
    return await TelegramUserService().confirm_send(_user_id(), UUID(request_id))


@mcp.tool()
async def telegram_get_messages(allowed_peer_id: int, limit: int = 20, after_message_id: int | None = None) -> list[dict[str, object]]:
    return await TelegramUserService().get_messages(_user_id(), allowed_peer_id, limit, after_message_id)


@mcp.tool()
async def telegram_get_new_messages(limit: int = 20) -> list[dict[str, object]]:
    """Return unread inbound messages from active allowlisted monitors."""
    return await TelegramUserService().get_new_messages(_user_id(), limit)


@mcp.tool()
def telegram_start_monitoring(allowed_peer_id: int, kind: str = "new_messages", anchor_message_id: int | None = None) -> dict[str, object]:
    return TelegramUserService().start_monitoring(_user_id(), allowed_peer_id, kind, anchor_message_id)


@mcp.tool()
def telegram_stop_monitoring(allowed_peer_id: int) -> dict[str, object]:
    return {"allowed_peer_id": allowed_peer_id, "status": "stopped" if TelegramUserService().stop_monitoring(_user_id(), allowed_peer_id) else "not_found"}
