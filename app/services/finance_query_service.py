"""Сервис выполнения структурированных финансовых запросов."""

from app.db.repositories.finance_repository import FinanceRepository
from app.schemas.finance_query_request import FinanceQueryMode, FinanceQueryRequest
from app.schemas.finance_query_result import (
    FinanceGroupResult,
    FinanceQueryResult,
    FinanceStatisticsResult,
    FinanceTransactionResult,
)


class FinanceQueryService:
    """Маршрутизирует режимы запроса к ORM-репозиторию финансов."""

    def __init__(self, repository: FinanceRepository):
        self.repository = repository

    def query(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        """Выполняет один из поддерживаемых режимов финансового запроса."""
        handler = getattr(self, f"_handle_{request.mode.value}")
        return handler(request)

    def _handle_total_amount(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        total = self.repository.get_total_amount(**self._filters(request))
        return FinanceQueryResult(
            mode=request.mode,
            answer=f"{total} ₽",
            total=total,
        )

    def _handle_transactions(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        rows = self.repository.get_transactions(
            **self._filters(request),
            limit=request.limit,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
        )
        return FinanceQueryResult(
            mode=request.mode,
            transactions=[FinanceTransactionResult.model_validate(row) for row in rows],
        )

    def _handle_group_by_category(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        rows = self.repository.get_group_by_category(
            **self._filters(request),
            limit=request.limit,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
        )
        return FinanceQueryResult(
            mode=request.mode,
            items=[FinanceGroupResult.model_validate(row) for row in rows],
        )

    def _handle_group_by_place(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        rows = self.repository.get_group_by_place(
            **self._filters(request),
            limit=request.limit,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
        )
        return FinanceQueryResult(
            mode=request.mode,
            items=[FinanceGroupResult.model_validate(row) for row in rows],
        )

    def _handle_statistics(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        statistics = FinanceStatisticsResult.model_validate(
            self.repository.get_statistics(**self._filters(request))
        )
        return FinanceQueryResult(mode=request.mode, statistics=statistics)

    def _handle_summary(self, request: FinanceQueryRequest) -> FinanceQueryResult:
        statistics = FinanceStatisticsResult.model_validate(
            self.repository.get_statistics(**self._filters(request))
        )
        groups = self.repository.get_group_by_category(
            **self._filters(request),
            limit=request.limit,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
        )
        return FinanceQueryResult(
            mode=request.mode,
            total=statistics.total,
            items=[FinanceGroupResult.model_validate(row) for row in groups],
            statistics=statistics,
        )

    @staticmethod
    def _filters(request: FinanceQueryRequest) -> dict[str, object]:
        """Извлекает общие фильтры, применимые ко всем режимам."""
        return {
            "category": request.category,
            "operation_type": request.operation_type,
            "product_name": request.product_name,
            "place": request.place,
            "date_from": request.date_from,
            "date_to": request.date_to,
        }
