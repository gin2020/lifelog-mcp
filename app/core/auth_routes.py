"""Public browser routes required by the Telegram OIDC authorization flow."""

import logging

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app.core.mcp import mcp, oauth_provider
from app.services.telegram_service import TelegramAuthenticationError


logger = logging.getLogger(__name__)


@mcp.custom_route("/auth/telegram/start", methods=["GET"], include_in_schema=False)
async def start_telegram_login(request: Request) -> Response:
    """Redirect a valid pending MCP authorization to Telegram OIDC."""
    if oauth_provider is None:
        return PlainTextResponse("Authentication is not enabled", status_code=404)
    request_id = request.query_params.get("request_id")
    if not request_id:
        return PlainTextResponse("Missing authorization request", status_code=400)
    try:
        return RedirectResponse(await oauth_provider.get_telegram_login_url(request_id))
    except TelegramAuthenticationError as error:
        logger.warning("Telegram login start failed: %s", error)
        return PlainTextResponse("Authorization request is invalid or expired", status_code=400)


@mcp.custom_route("/auth/telegram/callback", methods=["GET"], include_in_schema=False)
async def telegram_callback(request: Request) -> Response:
    """Validate Telegram Login and return the MCP client to its redirect URI."""
    if oauth_provider is None:
        return PlainTextResponse("Authentication is not enabled", status_code=404)
    request_id = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        logger.warning("Telegram login was rejected: %s", error)
        return PlainTextResponse("Telegram sign-in was not completed", status_code=400)
    if not request_id or not code:
        return PlainTextResponse("Missing Telegram authorization response", status_code=400)
    try:
        redirect_url = await oauth_provider.complete_telegram_login(
            request_id=request_id,
            state=request_id,
            code=code,
        )
    except TelegramAuthenticationError as error:
        logger.warning("Telegram login callback failed: %s", error)
        return PlainTextResponse("Telegram sign-in failed", status_code=400)
    return RedirectResponse(redirect_url)
