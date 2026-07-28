from app.db.repositories.finance_repository import FinanceRepository
from app.schemas.finance_query_request import FinanceQueryRequest
from app.schemas.finance_query_result import FinanceQueryResult


class FinanceQueryService:
    def __init__(self, repository: FinanceRepository):
        self.repository = repository

    def query(
        self,
        request: FinanceQueryRequest,
    ) -> FinanceQueryResult:

        handler = getattr(
            self,
            f"_handle_{request.intent.value}",
            None,
        )

        if handler is None:
            raise NotImplementedError(
                f"Unsupported intent: {request.intent}"
            )

        return handler(request)

    def _handle_total_amount(
        self,
        request: FinanceQueryRequest,
    ) -> FinanceQueryResult:

        total = self.repository.get_total_amount(
            category=request.category,
            operation_type=request.operation_type,
            date_from=request.date_from,
            date_to=request.date_to,
        )

        return FinanceQueryResult(
            answer=f"{total} ₽"
        )
