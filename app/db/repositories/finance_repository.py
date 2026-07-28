from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.finance_event import FinanceEvent, OperationType
from app.db.models.finance_item import FinanceCategory, FinanceItem


class FinanceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_total_amount(
        self,
        *,
        category: FinanceCategory | None = None,
        operation_type: OperationType | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Decimal:
        """
        Возвращает сумму финансовых операций по заданным фильтрам.
        """

        query = (
            select(func.sum(FinanceItem.total_price))
            .select_from(FinanceItem)
            .join(FinanceEvent)
        )

        if category is not None:
            query = query.where(
                FinanceItem.category == category
            )

        if operation_type is not None:
            query = query.where(
                FinanceEvent.operation_type == operation_type
            )

        if date_from is not None:
            query = query.where(
                FinanceEvent.created_at >= date_from
            )

        if date_to is not None:
            query = query.where(
                FinanceEvent.created_at <= date_to
            )

        result = self.db.scalar(query)

        return result or Decimal("0")
