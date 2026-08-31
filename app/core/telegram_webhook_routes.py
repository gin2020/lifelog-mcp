"""Telegram Bot API webhook endpoints, kept separate from Telegram OIDC routes."""

import logging
import secrets

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.config.settings import get_settings
from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.db.models.notification import TelegramNotificationSubscription
from app.db.models.user import User, UserIdentity
from app.services.telegram_bot_service import (
    TelegramBotApiError,
    TelegramBotConfigurationError,
    TelegramBotService,
)


logger = logging.getLogger(__name__)


@mcp.custom_route("/telegram/webhook", methods=["POST"], include_in_schema=False)
async def telegram_webhook(request: Request) -> Response:
    """Handle Telegram /start and /stop commands for notification consent."""
    settings = get_settings()
    secret = settings.telegram_webhook_secret
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret is None:
        logger.error("Rejected Telegram webhook: TELEGRAM_WEBHOOK_SECRET is not configured")
        return PlainTextResponse("Unauthorized", status_code=401)
    if received_secret is None:
        logger.warning("Rejected Telegram webhook: secret header is missing")
        return PlainTextResponse("Unauthorized", status_code=401)
    if not secrets.compare_digest(secret.get_secret_value(), received_secret):
        logger.warning("Rejected Telegram webhook: secret header does not match")
        return PlainTextResponse("Unauthorized", status_code=401)

    try:
        update = await request.json()
        message = update.get("message", {})
        text = message.get("text", "")
        sender = message.get("from", {})
        chat = message.get("chat", {})
        telegram_id = sender.get("id")
        chat_id = chat.get("id")
    except (AttributeError, ValueError):
        logger.warning("Ignored Telegram webhook: malformed JSON payload")
        return PlainTextResponse("ok")

    if not isinstance(text, str) or not isinstance(telegram_id, int) or not isinstance(chat_id, int):
        logger.info("Ignored Telegram webhook: message does not contain command, sender ID, and chat ID")
        return PlainTextResponse("ok")
    if chat.get("type") != "private":
        logger.info("Ignored Telegram webhook: command was sent outside a private chat")
        return PlainTextResponse("ok")

    command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0]
    if command not in {"/start", "/stop"}:
        logger.info("Ignored Telegram webhook: unsupported command=%s", command)
        return PlainTextResponse("ok")

    try:
        with SessionLocal() as session:
            user_id = session.scalar(
                select(User.id)
                .join(UserIdentity)
                .where(
                    UserIdentity.provider == "telegram",
                    UserIdentity.provider_subject == str(telegram_id),
                    User.is_active.is_(True),
                )
            )
            if user_id is None:
                logger.info(
                    "Telegram subscription refused: reason=telegram_identity_not_found telegram_id=%s",
                    telegram_id,
                )
                reply = "Сначала авторизуйтесь в Lifelog через Telegram Login, затем отправьте /start."
            elif command == "/start":
                subscription = session.get(TelegramNotificationSubscription, user_id)
                if subscription is None:
                    subscription = TelegramNotificationSubscription(
                        user_id=user_id,
                        chat_id=chat_id,
                        is_enabled=True,
                    )
                    session.add(subscription)
                    session.commit()
                    logger.info(
                        "Telegram subscription created: user_id=%s chat_id=%s",
                        user_id,
                        chat_id,
                    )
                else:
                    subscription.chat_id = chat_id
                    subscription.is_enabled = True
                    session.commit()
                    logger.info(
                        "Telegram subscription activated: user_id=%s chat_id=%s",
                        user_id,
                        chat_id,
                    )
                reply = "Уведомления Lifelog включены. Для отключения отправьте /stop."
            else:
                subscription = session.get(TelegramNotificationSubscription, user_id)
                if subscription is None:
                    logger.info(
                        "Telegram subscription stop ignored: reason=subscription_not_found user_id=%s",
                        user_id,
                    )
                else:
                    subscription.is_enabled = False
                    session.commit()
                    logger.info("Telegram subscription disabled: user_id=%s", user_id)
                reply = "Уведомления Lifelog отключены. Для включения отправьте /start."
    except Exception:
        logger.exception(
            "Telegram subscription operation failed: command=%s telegram_id=%s chat_id=%s",
            command,
            telegram_id,
            chat_id,
        )
        return PlainTextResponse("Internal Server Error", status_code=500)

    try:
        await TelegramBotService(settings).send_message(chat_id, reply)
    except (TelegramBotApiError, TelegramBotConfigurationError):
        logger.exception("Could not send Telegram webhook command response")
    return PlainTextResponse("ok")
