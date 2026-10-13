"""ORM models for personal MAX Web user sessions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Identity, Integer, String, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.types import PrimaryKeyInteger


class MaxAccount(Base):
    """Encrypted PyMax session belonging to one labelled LifeLog account."""

    __tablename__ = "max_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_max_account_user_label"),
        UniqueConstraint("max_user_id", name="uq_max_account_max_user_id"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'legacy'"))
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    device_id: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    display_name: Mapped[str | None] = mapped_column(String(512))
    session_ciphertext: Mapped[str | None] = mapped_column(Text)
    session_key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MaxAuthFlow(Base):
    """Short-lived QR flow metadata; the live PyMax client stays in memory."""

    __tablename__ = "max_auth_flows"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("max_accounts.id", ondelete="SET NULL"), index=True)
    account_label: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'qr_code_required'"), index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MaxAllowedChat(Base):
    """Explicit per-account allowlist entry addressed by stable MAX chat ID."""

    __tablename__ = "max_allowed_chats"
    __table_args__ = (UniqueConstraint("account_id", "max_chat_id", name="uq_max_allowed_chat"),)

    id: Mapped[int] = mapped_column(PrimaryKeyInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("max_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(512))
    chat_type: Mapped[str | None] = mapped_column(String(32))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MaxChatMonitor(Base):
    """Independent manual-read and notification cursors for one MAX chat."""

    __tablename__ = "max_chat_monitors"
    __table_args__ = (UniqueConstraint("account_id", "allowed_chat_id", name="uq_max_chat_monitor"),)

    id: Mapped[int] = mapped_column(PrimaryKeyInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("max_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed_chat_id: Mapped[int] = mapped_column(ForeignKey("max_allowed_chats.id", ondelete="CASCADE"), nullable=False)
    monitor_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'new_messages'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    anchor_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_notified_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_read_message_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class MaxSendRequest(Base):
    """Short-lived encrypted draft awaiting explicit send confirmation."""

    __tablename__ = "max_send_requests"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed_chat_id: Mapped[int] = mapped_column(ForeignKey("max_allowed_chats.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("max_accounts.id", ondelete="RESTRICT"), nullable=False, index=True)
    max_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    max_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
