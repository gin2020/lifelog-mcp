"""Схемы структурированных ответов финансовых запросов."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory
from app.schemas.finance_query_request import FinanceQueryMode


class FinanceTransactionResult(BaseModel):
    """Одна позиция финансовой операции."""

    event_id: int
    item_id: int
    date: datetime | None
    title: str
    category: FinanceCategory
    amount: Decimal
    place: str | None
    operation_type: OperationType
    currency: str
    quantity: Decimal
    unit: str


class FinanceGroupResult(BaseModel):
    """Агрегат по категории или месту покупки."""

    category: FinanceCategory | None = None
    place: str | None = None
    amount: Decimal
    transaction_count: int


class FinanceStatisticsResult(BaseModel):
    """Сводные показатели по отфильтрованным позициям."""

    transaction_count: int
    total: Decimal
    average_amount: Decimal | None
    min_amount: Decimal | None
    max_amount: Decimal | None


class FinanceQueryResult(BaseModel):
    """Результат одного из режимов финансового запроса."""

    mode: FinanceQueryMode
    answer: str | None = None
    total: Decimal | None = None
    transactions: list[FinanceTransactionResult] = Field(default_factory=list)
    items: list[FinanceGroupResult] = Field(default_factory=list)
    statistics: FinanceStatisticsResult | None = None
