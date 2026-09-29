from decimal import Decimal

from pydantic import BaseModel, model_validator

from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory

class FinanceItemCreate(BaseModel):
    name: str
    category: FinanceCategory
    quantity: Decimal
    unit: str
    total_price: Decimal


class FinanceEventCreate(BaseModel):
    operation_type: str
    place: str | None = None
    currency: str = "RUB"
    total_amount: Decimal
    items: list[FinanceItemCreate]


class FinanceEventUpdate(BaseModel):
    """Fields that may be changed on a finance event itself."""

    operation_type: OperationType | None = None
    place: str | None = None
    currency: str | None = None

    @model_validator(mode="after")
    def require_change(self) -> "FinanceEventUpdate":
        """Reject an update that does not change anything."""
        if (
            self.operation_type is None
            and self.place is None
            and self.currency is None
        ):
            raise ValueError("At least one finance event field must be provided")
        return self


class FinanceItemUpdate(BaseModel):
    """Fields that may be changed on one finance item."""

    name: str | None = None
    category: FinanceCategory | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    total_price: Decimal | None = None

    @model_validator(mode="after")
    def require_change(self) -> "FinanceItemUpdate":
        """Reject an update that does not change anything."""
        if all(
            value is None
            for value in (
                self.name,
                self.category,
                self.quantity,
                self.unit,
                self.total_price,
            )
        ):
            raise ValueError("At least one finance item field must be provided")
        return self
