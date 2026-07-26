"""Проверка доступности PostgreSQL через инфраструктуру SQLAlchemy."""

from app.db.database import engine


def test_database_connection() -> None:
    """Открывает и закрывает соединение с PostgreSQL."""
    with engine.connect():
        pass
