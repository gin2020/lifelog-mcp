"""ORM-модель позиции финансового события."""

from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Enum as SqlEnum,
    ForeignKey,
    Identity,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class FinanceCategory(str, Enum):
    """Категория товара."""

    DAIRY = "dairy"
    MEAT = "meat"
    VEGETABLES = "vegetables"
    FRUITS = "fruits"
    DRINKS = "drinks"
    HOUSEHOLD = "household"
    OTHER = "other"


class FinanceItem(Base):
    """Позиция финансового события."""

    __tablename__ = "finance_items"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey(
            "finance_events.id",
            ondelete="CASCADE",
        )
    )

    name: Mapped[str] = mapped_column(
        String(255)
    )

    category: Mapped[FinanceCategory] = mapped_column(
        SqlEnum(FinanceCategory),
        default=FinanceCategory.OTHER,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(10, 3)
    )

    unit: Mapped[str] = mapped_column(
        String(32)
    )

    total_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2)
    )

    event: Mapped["FinanceEvent"] = relationship(
        back_populates="items",
    )
