"""Настройки приложения, загружаемые из переменных окружения."""

import base64
import binascii
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация, необходимая для инфраструктуры приложения."""

    database_url: PostgresDsn
    default_user_telegram_id: int
    auth_enabled: bool = False
    oauth_issuer_url: AnyHttpUrl | None = None
    mcp_resource_url: AnyHttpUrl | None = None
    telegram_client_id: str | None = None
    telegram_client_secret: SecretStr | None = None
    telegram_redirect_uri: AnyHttpUrl | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_url: AnyHttpUrl | None = None
    jwt_signing_key: SecretStr | None = None
    jwt_access_token_ttl_seconds: int = 3600
    oauth_refresh_token_ttl_seconds: int = 2_592_000
    backup_enabled: bool = False
    backup_time: str = "09:00"
    backup_timezone: str = "Europe/Berlin"
    backup_encryption_key: SecretStr | None = None
    backup_state_file: str = ".backup-state.json"
    backup_lock_file: str = ".backup.lock"
    backup_http_timeout_seconds: float = 120.0
    backup_max_file_size_bytes: int = 50 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        """Require complete OAuth configuration when authentication is enabled."""
        if not self.auth_enabled:
            return self

        required = {
            "OAUTH_ISSUER_URL": self.oauth_issuer_url,
            "MCP_RESOURCE_URL": self.mcp_resource_url,
            "TELEGRAM_CLIENT_ID": self.telegram_client_id,
            "TELEGRAM_CLIENT_SECRET": self.telegram_client_secret,
            "TELEGRAM_REDIRECT_URI": self.telegram_redirect_uri,
            "JWT_SIGNING_KEY": self.jwt_signing_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                "Authentication is enabled but required settings are missing: "
                + ", ".join(missing)
            )
        return self

    @model_validator(mode="after")
    def validate_backup_settings(self) -> "Settings":
        """Validate backup settings only when the standalone job is enabled."""
        try:
            datetime.strptime(self.backup_time, "%H:%M")
        except ValueError as error:
            raise ValueError("BACKUP_TIME must use HH:MM format") from error

        try:
            ZoneInfo(self.backup_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("BACKUP_TIMEZONE must be a valid IANA timezone") from error

        if self.backup_http_timeout_seconds <= 0:
            raise ValueError("BACKUP_HTTP_TIMEOUT_SECONDS must be positive")
        if self.backup_max_file_size_bytes <= 0:
            raise ValueError("BACKUP_MAX_FILE_SIZE_BYTES must be positive")

        if not self.backup_enabled:
            return self
        if self.backup_encryption_key is None or not self.backup_encryption_key.get_secret_value().strip():
            raise ValueError(
                "BACKUP_ENABLED is true but BACKUP_ENCRYPTION_KEY is missing"
            )
        try:
            encoded = self.backup_encryption_key.get_secret_value().strip()
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, binascii.Error) as error:
            raise ValueError("BACKUP_ENCRYPTION_KEY must be URL-safe base64") from error
        if len(decoded) != 32:
            raise ValueError("BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    """Возвращает единый экземпляр настроек приложения."""
    return Settings()
