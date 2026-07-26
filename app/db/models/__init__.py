"""ORM-модели приложения.

Добавляйте импорт каждой будущей модели в этот модуль, чтобы Alembic включал её
в ``Base.metadata`` при автогенерации миграций.
"""

from app.db.models.memory import Memory

__all__ = ["Memory"]
