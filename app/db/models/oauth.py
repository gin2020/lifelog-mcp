"""ORM-модели состояния OAuth для MCP-клиентов."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class OAuthClient(Base):
    """Динамически зарегистрированный публичный OAuth-клиент MCP."""

    __tablename__ = "oauth_clients"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    client_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OAuthAuthorizationRequest(Base):
    """Незавершённый OAuth-запрос MCP, ожидающий входа через Telegram."""

    __tablename__ = "oauth_authorization_requests"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    client_state: Mapped[str | None] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(nullable=False)
    resource: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    telegram_code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OAuthAuthorizationCode(Base):
    """Одноразовый authorization code, выданный после успешного Telegram Login."""

    __tablename__ = "oauth_authorization_codes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(nullable=False)
    resource: Mapped[str | None] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OAuthRefreshToken(Base):
    """Хеш ротируемого refresh token для длительной MCP-сессии."""

    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
