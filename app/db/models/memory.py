"""ORM-модель записи памяти."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.db.types import PrimaryKeyInteger


class Memory(Base):
    """Базовая запись памяти."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(PrimaryKeyInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    uuid: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # This is deliberately distinct from created_at: it records when the
    # remembered fact or event happened, if the caller knows it.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("ix_memories_user_occurred_at", "user_id", "occurred_at"),
    )
