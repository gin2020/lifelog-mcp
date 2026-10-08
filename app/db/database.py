"""Инфраструктура SQLAlchemy для подключения к PostgreSQL."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    """Базовый класс будущих ORM-моделей."""


database_url = str(get_settings().database_url)
engine_options = {"pool_pre_ping": True}
if database_url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
    if ":memory:" in database_url:
        engine_options["poolclass"] = StaticPool

engine = create_engine(database_url, **engine_options)

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
