from decimal import Decimal

from pydantic import BaseModel

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
