"""MCP-инструмент для запросов к финансовым данным."""

from datetime import datetime
import logging

from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory
from app.db.repositories.finance_repository import FinanceRepository
from app.schemas.finance_query_request import FinanceIntent, FinanceQueryRequest
from app.schemas.finance_query_result import FinanceQueryResult
from app.services.finance_query_service import FinanceQueryService

logger = logging.getLogger(__name__)


@mcp.tool()
def query_finance(
    category: FinanceCategory | None = None,
    operation_type: OperationType = OperationType.EXPENSE,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> FinanceQueryResult:
    """Return the total amount of recorded financial operations.

    Use ``category="meat"`` and the default ``operation_type="expense"``
    for questions such as "How much did I spend on meat?". The tool only
    queries PostgreSQL and does not perform inference or classification.
    """
    request = FinanceQueryRequest(
        intent=FinanceIntent.TOTAL_AMOUNT,
        category=category,
        operation_type=operation_type,
        date_from=date_from,
        date_to=date_to,
    )

    logger.info(
        "MCP tool called: tool=query_finance category=%r operation_type=%r "
        "date_from=%r date_to=%r",
        category,
        operation_type,
        date_from,
        date_to,
    )

    try:
        with SessionLocal() as session:
            logger.info(
                "MCP tool entered FinanceQueryService: tool=query_finance"
            )

            repository = FinanceRepository(session)
            service = FinanceQueryService(repository)

            result = service.query(request)

    except Exception:
        logger.exception(
            "MCP tool failed: tool=query_finance"
        )
        raise

    logger.info(
        "MCP tool succeeded: tool=query_finance"
    )

    return result
