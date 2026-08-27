"""ORM-модели приложения.

Добавляйте импорт каждой будущей модели в этот модуль, чтобы Alembic включал её
в ``Base.metadata`` при автогенерации миграций.
"""

"""ORM-модели приложения."""

from app.db.models.finance_event import FinanceEvent
from app.db.models.finance_item import FinanceItem
from app.db.models.memory import Memory
from app.db.models.user import User, UserIdentity

__all__ = [
    "Memory",
    "FinanceEvent",
    "FinanceItem",
    "User",
    "UserIdentity",
]
