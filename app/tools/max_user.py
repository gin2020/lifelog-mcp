"""MCP tools for a user's personal MAX account through the unofficial user API."""

from uuid import UUID

from app.core.dependencies import get_request_user_id
from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.services.max_user_auth import MaxUserAuthError, MaxUserAuthService
from app.services.max_user_service import MaxUserService, MaxUserServiceError


def _user_id() -> int:
    with SessionLocal() as db:
        return get_request_user_id(db)


@mcp.tool()
async def max_connect(operation: str, flow_id: str | None = None, account_id: int | None = None, account_label: str | None = None) -> dict[str, object]:
    """Start or continue staged personal MAX QR authorization."""
    service = MaxUserAuthService()
    user_id = _user_id()
    try:
        if operation == "qr_start":
            return await service.qr_start(user_id, account_id, account_label)
        if flow_id is None:
            raise MaxUserAuthError("flow_id is required")
        parsed = UUID(flow_id)
        if operation == "qr_status":
            return await service.qr_status(user_id, parsed)
        if operation == "cancel":
            return {"flow_id": flow_id, "status": "cancelled" if await service.cancel(user_id, parsed) else "not_found"}
        raise MaxUserAuthError("operation must be qr_start, qr_status, or cancel")
    except (MaxUserAuthError, ValueError) as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def max_status(account_id: int | None = None) -> dict[str, object]:
    return MaxUserAuthService().status(_user_id(), account_id)


@mcp.tool()
def max_list_accounts() -> list[dict[str, object]]:
    return MaxUserAuthService().list_accounts(_user_id())


@mcp.tool()
def max_disconnect(account_id: int | None = None) -> dict[str, object]:
    service = MaxUserAuthService()
    user_id = _user_id()
    before = service.status(user_id, account_id)
    disconnected = service.disconnect(user_id, account_id)
    return {"account_id": before.get("account_id", account_id), "account_label": before.get("account_label"), "status": "disconnected" if disconnected else "not_connected"}


@mcp.tool()
async def max_list_chats(account_id: int | None = None, query: str | None = None) -> list[dict[str, object]]:
    return await MaxUserService().list_chats(_user_id(), account_id, query)


@mcp.tool()
async def max_add_allowed_chat(max_chat_id: int, account_id: int | None = None) -> dict[str, object]:
    return await MaxUserService().add_allowed_chat(_user_id(), max_chat_id, account_id)


@mcp.tool()
def max_list_allowed_chats(account_id: int | None = None) -> list[dict[str, object]]:
    return MaxUserService().list_allowed_chats(_user_id(), account_id)


@mcp.tool()
def max_remove_allowed_chat(allowed_chat_id: int) -> dict[str, object]:
    removed = MaxUserService().remove_allowed_chat(_user_id(), allowed_chat_id)
    return {"allowed_chat_id": allowed_chat_id, "status": "removed" if removed else "not_found"}


@mcp.tool()
def max_send_message(allowed_chat_id: int, text: str, account_id: int | None = None) -> dict[str, object]:
    return MaxUserService().create_send_request(_user_id(), allowed_chat_id, text, account_id)


@mcp.tool()
async def max_confirm_send(request_id: str) -> dict[str, object]:
    try:
        return await MaxUserService().confirm_send(_user_id(), UUID(request_id))
    except (ValueError, MaxUserServiceError) as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def max_cancel_send(request_id: str) -> dict[str, object]:
    try:
        return MaxUserService().cancel_send(_user_id(), UUID(request_id))
    except (ValueError, MaxUserServiceError) as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
async def max_get_messages(allowed_chat_id: int, limit: int = 20, after_message_id: int | None = None, account_id: int | None = None) -> list[dict[str, object]]:
    try:
        return await MaxUserService().get_messages(_user_id(), allowed_chat_id, limit, after_message_id, account_id)
    except MaxUserServiceError as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
async def max_get_new_messages(limit: int = 20, account_id: int | None = None) -> list[dict[str, object]]:
    try:
        return await MaxUserService().get_new_messages(_user_id(), limit, account_id)
    except MaxUserServiceError as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def max_start_monitoring(allowed_chat_id: int, kind: str = "new_messages", anchor_message_id: int | None = None, account_id: int | None = None) -> dict[str, object]:
    try:
        return MaxUserService().start_monitoring(_user_id(), allowed_chat_id, kind, anchor_message_id, account_id)
    except MaxUserServiceError as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def max_stop_monitoring(allowed_chat_id: int, account_id: int | None = None) -> dict[str, object]:
    stopped = MaxUserService().stop_monitoring(_user_id(), allowed_chat_id, account_id)
    return {"allowed_chat_id": allowed_chat_id, "account_id": account_id, "status": "stopped" if stopped else "not_found"}
