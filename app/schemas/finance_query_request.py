"""Схемы структурированных запросов к финансовым данным."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory


class FinanceQueryMode(str, Enum):
    """Поддерживаемые режимы ответа финансового запроса."""

    TOTAL_AMOUNT = "total_amount"
    TRANSACTIONS = "transactions"
    GROUP_BY_CATEGORY = "group_by_category"
    GROUP_BY_PLACE = "group_by_place"
    STATISTICS = "statistics"
    SUMMARY = "summary"


class FinanceSortField(str, Enum):
    """Поля, доступные для сортировки результатов."""

    DATE = "date"
    AMOUNT = "amount"
    TITLE = "title"
    PLACE = "place"
    CATEGORY = "category"
    COUNT = "count"


class SortDirection(str, Enum):
    """Направление сортировки."""

    ASC = "asc"
    DESC = "desc"


class FinanceQueryRequest(BaseModel):
    """Нормализованный запрос, собранный MCP-инструментом."""

    mode: FinanceQueryMode = FinanceQueryMode.TOTAL_AMOUNT
    operation_type: OperationType | None = OperationType.EXPENSE
    category: FinanceCategory | None = None
    product_name: str | None = None
    place: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)
    sort_by: FinanceSortField = FinanceSortField.DATE
    sort_direction: SortDirection = SortDirection.DESC
