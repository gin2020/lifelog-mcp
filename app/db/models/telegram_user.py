"""ORM models for per-user Telegram MTProto sessions and access control."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Integer, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TelegramAccount(Base):
    """Encrypted MTProto session belonging to exactly one LifeLog user."""

    __tablename__ = "telegram_accounts"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_telegram_account_user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(512))
    session_ciphertext: Mapped[str | None] = mapped_column(Text)
    session_key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TelegramAuthFlow(Base):
    """Short-lived state for staged phone/code authentication."""

    __tablename__ = "telegram_auth_flows"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    session_ciphertext: Mapped[str | None] = mapped_column(Text)
    phone_code_hash_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'code_required'"), index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TelegramAllowedPeer(Base):
    """Explicit per-user allowlist entry addressed by stable Telegram peer ID."""

    __tablename__ = "telegram_allowed_peers"
    __table_args__ = (UniqueConstraint("user_id", "peer_type", "telegram_peer_id", name="uq_telegram_allowed_peer"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    peer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(512))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TelegramDialogueMonitor(Base):
    """Watermark for a bounded monitor over an allowlisted dialogue."""

    __tablename__ = "telegram_dialogue_monitors"
    __table_args__ = (UniqueConstraint("user_id", "allowed_peer_id", name="uq_telegram_dialogue_monitor"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed_peer_id: Mapped[int] = mapped_column(ForeignKey("telegram_allowed_peers.id", ondelete="CASCADE"), nullable=False)
    monitor_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'new_messages'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    anchor_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_notified_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class TelegramSendRequest(Base):
    """Short-lived, encrypted message draft awaiting explicit confirmation."""

    __tablename__ = "telegram_send_requests"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed_peer_id: Mapped[int] = mapped_column(ForeignKey("telegram_allowed_peers.id", ondelete="RESTRICT"), nullable=False)
    telegram_peer_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
