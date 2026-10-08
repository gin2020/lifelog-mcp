"""Проверка доступности PostgreSQL через инфраструктуру SQLAlchemy."""

from app.db.database import engine

import pytest


pytestmark = pytest.mark.integration


def test_database_connection() -> None:
    """Открывает и закрывает соединение с явно переданной integration БД."""
    with engine.connect():
        pass
