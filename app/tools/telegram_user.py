"""MCP tools for a user's Telegram account through MTProto."""

from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.dependencies import get_request_user_id
from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.services.telegram_user_auth import TelegramUserAuthError, TelegramUserAuthService
from app.services.telegram_user_service import TelegramUserService, TelegramUserServiceError


TELEGRAM_SEND_CONFIRMATION_UI_URI = "ui://telegram/send-confirmation-v1.html"
TELEGRAM_SEND_CONFIRMATION_UI_PATH = Path(__file__).resolve().parents[1] / "ui" / "telegram_send_confirmation.html"
TELEGRAM_SEND_CONFIRMATION_UI_DOMAIN = "https://mcp.jesarion.com"
TELEGRAM_SEND_CONFIRMATION_TOOL_META = {
    "ui": {
        "resourceUri": TELEGRAM_SEND_CONFIRMATION_UI_URI,
        "visibility": ["model", "app"],
    },
    "openai/outputTemplate": TELEGRAM_SEND_CONFIRMATION_UI_URI,
}
TELEGRAM_SEND_ACTION_META = {
    "ui": {"visibility": ["model", "app"]},
    "openai/widgetAccessible": True,
}


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
            return {"flow_id": flow_id, "status": "cancelled" if await service.cancel(user_id, parsed) else "not_found"}
        if operation == "submit_code":
            if code is None:
                raise TelegramUserAuthError("code is required")
            return await service.submit_code(user_id, parsed, code)
        if operation == "submit_2fa":
            if password is None:
                raise TelegramUserAuthError("password is required")
            return await service.submit_2fa(user_id, parsed, password)
        raise TelegramUserAuthError(
            "operation must be start, submit_code, submit_2fa, qr_start, qr_status, or cancel"
        )
    except (TelegramUserAuthError, ValueError) as error:
        raise RuntimeError(str(error)) from error


@mcp.tool()
def telegram_status() -> dict[str, object]:
    return TelegramUserAuthService().status(_user_id())


@mcp.tool()
def telegram_disconnect() -> dict[str, str]:
    return {"status": "disconnected" if TelegramUserAuthService().disconnect(_user_id()) else "not_connected"}


@mcp.tool()
async def telegram_search_contacts(query: str) -> dict[str, object]:
    """Search the connected user's Telegram contacts or exact peer identifiers."""
    return await TelegramUserService().search_contacts(_user_id(), query)


@mcp.tool()
async def telegram_add_allowed_contact(
    reference: str | int | None = None,
    telegram_peer_id: int | None = None,
) -> dict[str, object]:
    if reference is not None and telegram_peer_id is not None:
        raise TelegramUserServiceError("Provide either reference or telegram_peer_id, not both")
    if reference is None:
        reference = telegram_peer_id
    if reference is None:
        raise TelegramUserServiceError("reference or telegram_peer_id is required")
    return await TelegramUserService().add_allowed_peer(_user_id(), reference)


@mcp.tool()
def telegram_list_allowed_contacts() -> list[dict[str, object]]:
    return TelegramUserService().list_allowed_peers(_user_id())


@mcp.tool()
def telegram_remove_allowed_contact(allowed_peer_id: int) -> dict[str, object]:
    return {"allowed_peer_id": allowed_peer_id, "status": "removed" if TelegramUserService().remove_allowed_peer(_user_id(), allowed_peer_id) else "not_found"}


@mcp.tool(meta=TELEGRAM_SEND_CONFIRMATION_TOOL_META)
def telegram_send_message(allowed_peer_id: int, text: str) -> dict[str, object]:
    return TelegramUserService().create_send_request(_user_id(), allowed_peer_id, text)


@mcp.tool(meta=TELEGRAM_SEND_ACTION_META)
async def telegram_confirm_send(request_id: str) -> dict[str, object]:
    return await TelegramUserService().confirm_send(_user_id(), UUID(request_id))


@mcp.tool(meta=TELEGRAM_SEND_ACTION_META)
def telegram_cancel_send(request_id: str) -> dict[str, object]:
    return TelegramUserService().cancel_send(_user_id(), UUID(request_id))


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


@mcp.resource(
    TELEGRAM_SEND_CONFIRMATION_UI_URI,
    name="telegram-send-confirmation",
    description="Interactive confirmation card for a prepared Telegram message.",
    mime_type="text/html;profile=mcp-app",
    meta={
        "ui": {
            "prefersBorder": True,
            "domain": TELEGRAM_SEND_CONFIRMATION_UI_DOMAIN,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
            },
        },
        "openai/widgetDescription": "Confirms the exact Telegram recipient and message before sending.",
    },
)
def telegram_send_confirmation_resource() -> str:
    return TELEGRAM_SEND_CONFIRMATION_UI_PATH.read_text(encoding="utf-8")
