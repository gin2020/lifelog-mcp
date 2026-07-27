from decimal import Decimal

from pydantic import BaseModel


class FinanceItemCreate(BaseModel):
    name: str
    category: str
    quantity: Decimal
    unit: str
    total_price: Decimal


class FinanceEventCreate(BaseModel):
    operation_type: str
    place: str | None = None
    currency: str = "RUB"
    total_amount: Decimal
    items: list[FinanceItemCreate]
