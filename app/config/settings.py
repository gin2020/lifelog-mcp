"""Настройки приложения, загружаемые из переменных окружения."""

from functools import lru_cache

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
    jwt_signing_key: SecretStr | None = None
    jwt_access_token_ttl_seconds: int = 3600
    oauth_refresh_token_ttl_seconds: int = 2_592_000

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


@lru_cache
def get_settings() -> Settings:
    """Возвращает единый экземпляр настроек приложения."""
    return Settings()
