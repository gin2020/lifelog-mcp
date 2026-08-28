"""Зависимости приложения для временного однопользовательского контекста."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from mcp.server.auth.middleware.auth_context import get_access_token
from uuid import UUID

from app.config.settings import get_settings
from app.db.models.user import User, UserIdentity


def get_default_user_id(session: Session) -> int:
    """Return the configured bootstrap user's internal identifier.

    Until OAuth is introduced in phase 2, every MCP request is intentionally
    scoped to this user. The configured Telegram ID is only an ownership key;
    it is not used for authentication in this phase.
    """
    telegram_id = str(get_settings().default_user_telegram_id)
    statement = (
        select(User.id)
        .join(UserIdentity)
        .where(
            UserIdentity.provider == "telegram",
            UserIdentity.provider_subject == telegram_id,
            User.is_active.is_(True),
        )
    )
    user_id = session.scalar(statement)
    if user_id is None:
        raise RuntimeError(
            "Bootstrap user is not configured in the database. "
            "Apply the multi-user Alembic migration first."
        )
    return user_id


def get_request_user_id(session: Session) -> int:
    """Return the authenticated user, or the phase-1 bootstrap user while auth is off."""
    settings = get_settings()
    token = get_access_token()
    if token is None:
        if settings.auth_enabled:
            raise RuntimeError("An authenticated MCP access token is required")
        return get_default_user_id(session)

    if token.subject is None:
        raise RuntimeError("MCP access token does not identify a user")
    try:
        user_uuid = UUID(token.subject)
    except ValueError as error:
        raise RuntimeError("MCP access token user identifier is invalid") from error
    statement = select(User.id).where(
        User.uuid == user_uuid,
        User.is_active.is_(True),
    )
    user_id = session.scalar(statement)
    if user_id is None:
        raise RuntimeError("MCP access token user is unavailable")
    return user_id
