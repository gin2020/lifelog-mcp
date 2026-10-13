"""Application-level encryption for MAX user sessions and message drafts."""

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import Settings, get_settings


class MaxSessionCryptoError(ValueError):
    """Raised when the configured MAX encryption key is invalid."""


class MaxSessionCrypto:
    """Encrypt MAX session files and short-lived message drafts at rest."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        key = settings.max_session_encryption_key
        if key is None:
            raise MaxSessionCryptoError("MAX_SESSION_ENCRYPTION_KEY is not configured")
        try:
            self._fernet = Fernet(key.get_secret_value())
        except (ValueError, TypeError) as error:
            raise MaxSessionCryptoError("MAX_SESSION_ENCRYPTION_KEY must be a Fernet key") from error

    def encrypt(self, value: str | bytes) -> str:
        raw = value if isinstance(value, bytes) else value.encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, value: str) -> bytes:
        try:
            return self._fernet.decrypt(value.encode("ascii"))
        except (InvalidToken, ValueError) as error:
            raise MaxSessionCryptoError("MAX encrypted value is invalid") from error
