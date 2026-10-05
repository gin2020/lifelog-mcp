"""Application-level encryption for Telegram sessions and pending message text."""

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import Settings, get_settings


class TelegramSessionCryptoError(ValueError):
    """Raised when the configured Telegram encryption key is invalid."""


class TelegramSessionCrypto:
    """Encrypt/decrypt sensitive Telegram data without exposing plaintext to the DB."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        key = settings.telegram_session_encryption_key
        if key is None:
            raise TelegramSessionCryptoError("TELEGRAM_SESSION_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(key.get_secret_value())
        except (ValueError, TypeError) as error:
            raise TelegramSessionCryptoError("TELEGRAM_SESSION_ENCRYPTION_KEY must be a Fernet key") from error

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise TelegramSessionCryptoError("Telegram encrypted value is invalid") from error
