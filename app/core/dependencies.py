"""Зависимости приложения для временного однопользовательского контекста."""

from sqlalchemy import select
from sqlalchemy.orm import Session

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
