"""Инфраструктура SQLAlchemy для подключения к PostgreSQL."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    """Базовый класс будущих ORM-моделей."""


engine = create_engine(
    str(get_settings().database_url),
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db_session() -> Generator[Session, None, None]:
    """Предоставляет сессию БД и гарантированно закрывает её."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
