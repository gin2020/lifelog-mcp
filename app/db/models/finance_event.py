"""ORM-модель финансового события."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Identity,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.types import PrimaryKeyInteger


class OperationType(str, Enum):
    """Тип финансовой операции."""

    EXPENSE = "expense"
    INCOME = "income"


class FinanceEvent(Base):
    """Финансовое событие (одна операция)."""

    __tablename__ = "finance_events"

    id: Mapped[int] = mapped_column(
        PrimaryKeyInteger,
        Identity(),
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    uuid: Mapped[UUID | None] = mapped_column(
        Uuid,
        unique=True,
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    operation_type: Mapped[OperationType] = mapped_column(
        SqlEnum(OperationType)
    )

    place: Mapped[str | None] = mapped_column(
        String(255)
    )

    currency: Mapped[str] = mapped_column(
        String(8),
        default="RUB",
    )

    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2)
    )

    items: Mapped[list["FinanceItem"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
    )
