"""ORM-репозиторий для чтения финансовых данных."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.db.models.finance_event import FinanceEvent, OperationType
from app.db.models.finance_item import FinanceCategory, FinanceItem
from app.schemas.finance_query_request import FinanceSortField, SortDirection


class FinanceRepository:
    """Выполняет детерминированные ORM-запросы к финансовым операциям."""

    def __init__(self, db: Session, user_id: int):
        self.db = db
        self._user_id = user_id

    def get_total_amount(
        self,
        **filters: object,
    ) -> Decimal:
        """Возвращает сумму позиций с применёнными фильтрами."""
        statement = self._filtered_items_statement(
            select(func.sum(FinanceItem.total_price)),
            **filters,
        )
        return self.db.scalar(statement) or Decimal("0")

    def get_transactions(
        self,
        *,
        limit: int,
        sort_by: FinanceSortField,
        sort_direction: SortDirection,
        **filters: object,
    ) -> list[dict[str, object]]:
        """Возвращает отфильтрованные позиции операций."""
        statement = select(
            FinanceEvent.id.label("event_id"),
            FinanceItem.id.label("item_id"),
            FinanceEvent.created_at.label("date"),
            FinanceItem.name.label("title"),
            FinanceItem.category,
            FinanceItem.total_price.label("amount"),
            FinanceEvent.place,
            FinanceEvent.operation_type,
            FinanceEvent.currency,
            FinanceItem.quantity,
            FinanceItem.unit,
        )
        statement = self._filtered_items_statement(statement, **filters)
        order_fields = {
            FinanceSortField.DATE: FinanceEvent.created_at,
            FinanceSortField.AMOUNT: FinanceItem.total_price,
            FinanceSortField.TITLE: FinanceItem.name,
            FinanceSortField.PLACE: FinanceEvent.place,
            FinanceSortField.CATEGORY: FinanceItem.category,
        }
        order_field = order_fields.get(sort_by, FinanceEvent.created_at)
        order = asc(order_field) if sort_direction is SortDirection.ASC else desc(order_field)
        statement = statement.order_by(order, desc(FinanceItem.id)).limit(limit)
        return [dict(row) for row in self.db.execute(statement).mappings()]

    def get_group_by_category(
        self,
        *,
        limit: int,
        sort_by: FinanceSortField,
        sort_direction: SortDirection,
        **filters: object,
    ) -> list[dict[str, object]]:
        """Возвращает суммы и количество позиций по категориям."""
        amount = func.sum(FinanceItem.total_price).label("amount")
        transaction_count = func.count(FinanceItem.id).label("transaction_count")
        statement = select(FinanceItem.category, amount, transaction_count)
        statement = self._filtered_items_statement(statement, **filters).group_by(
            FinanceItem.category
        )
        statement = statement.order_by(
            self._group_order(
                sort_by,
                sort_direction,
                category_field=FinanceItem.category,
                place_field=None,
                amount=amount,
                transaction_count=transaction_count,
            )
        ).limit(limit)
        return [dict(row) for row in self.db.execute(statement).mappings()]

    def get_group_by_place(
        self,
        *,
        limit: int,
        sort_by: FinanceSortField,
        sort_direction: SortDirection,
        **filters: object,
    ) -> list[dict[str, object]]:
        """Возвращает суммы и количество позиций по местам покупки."""
        amount = func.sum(FinanceItem.total_price).label("amount")
        transaction_count = func.count(FinanceItem.id).label("transaction_count")
        statement = select(FinanceEvent.place, amount, transaction_count)
        statement = self._filtered_items_statement(statement, **filters).group_by(
            FinanceEvent.place
        )
        statement = statement.order_by(
            self._group_order(
                sort_by,
                sort_direction,
                category_field=None,
                place_field=FinanceEvent.place,
                amount=amount,
                transaction_count=transaction_count,
            )
        ).limit(limit)
        return [dict(row) for row in self.db.execute(statement).mappings()]

    def get_statistics(self, **filters: object) -> dict[str, object]:
        """Возвращает базовую статистику по позициям операций."""
        statement = select(
            func.count(FinanceItem.id).label("transaction_count"),
            func.sum(FinanceItem.total_price).label("total"),
            func.avg(FinanceItem.total_price).label("average_amount"),
            func.min(FinanceItem.total_price).label("min_amount"),
            func.max(FinanceItem.total_price).label("max_amount"),
        )
        statement = self._filtered_items_statement(statement, **filters)
        row = self.db.execute(statement).mappings().one()
        return dict(row)

    def _filtered_items_statement(self, statement, **filters: object):
        """Применяет общие фильтры к запросу позиций финансовых операций."""
        statement = statement.select_from(FinanceItem).join(FinanceEvent)
        statement = statement.where(FinanceEvent.user_id == self._user_id)
        category = filters.get("category")
        operation_type = filters.get("operation_type")
        product_name = filters.get("product_name")
        place = filters.get("place")
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")

        if category is not None:
            statement = statement.where(FinanceItem.category == category)
        if operation_type is not None:
            statement = statement.where(FinanceEvent.operation_type == operation_type)
        if product_name is not None:
            statement = statement.where(FinanceItem.name.ilike(f"%{product_name}%"))
        if place is not None:
            statement = statement.where(FinanceEvent.place.ilike(f"%{place}%"))
        if date_from is not None:
            statement = statement.where(FinanceEvent.created_at >= date_from)
        if date_to is not None:
            statement = statement.where(FinanceEvent.created_at <= date_to)
        return statement

    @staticmethod
    def _group_order(
        sort_by: FinanceSortField,
        sort_direction: SortDirection,
        *,
        category_field,
        place_field,
        amount,
        transaction_count,
    ):
        """Выбирает поле сортировки для агрегированного результата."""
        if sort_by is FinanceSortField.CATEGORY and category_field is not None:
            field = category_field
        elif sort_by is FinanceSortField.PLACE and place_field is not None:
            field = place_field
        elif sort_by is FinanceSortField.COUNT:
            field = transaction_count
        else:
            field = amount
        return asc(field) if sort_direction is SortDirection.ASC else desc(field)
