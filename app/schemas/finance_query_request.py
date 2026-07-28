from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory


class FinanceIntent(str, Enum):
    TOTAL_AMOUNT = "total_amount"
    ITEMS = "items"
    CATEGORY_STATISTICS = "category_statistics"


class FinanceQueryRequest(BaseModel):
    intent: FinanceIntent

    operation_type: OperationType | None = None
    category: FinanceCategory | None = None

    date_from: datetime | None = None
    date_to: datetime | None = None

    place: str | None = None
    product_name: str | None = None
