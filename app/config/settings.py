"""Настройки приложения, загружаемые из переменных окружения."""

from functools import lru_cache

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация, необходимая для инфраструктуры приложения."""

    database_url: PostgresDsn
    default_user_telegram_id: int

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает единый экземпляр настроек приложения."""
    return Settings()
