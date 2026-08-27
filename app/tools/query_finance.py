"""MCP-инструмент для запросов к финансовым данным."""

from datetime import datetime
import logging
from typing import Annotated

from pydantic import Field

from app.core.mcp import mcp
from app.core.dependencies import get_default_user_id
from app.db.database import SessionLocal
from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory
from app.db.repositories.finance_repository import FinanceRepository
from app.schemas.finance_query_request import (
    FinanceQueryMode,
    FinanceQueryRequest,
    FinanceSortField,
    SortDirection,
)
from app.schemas.finance_query_result import FinanceQueryResult
from app.services.finance_query_service import FinanceQueryService

logger = logging.getLogger(__name__)


@mcp.tool()
def query_finance(
    mode: FinanceQueryMode = FinanceQueryMode.TOTAL_AMOUNT,
    category: FinanceCategory | None = None,
    operation_type: OperationType = OperationType.EXPENSE,
    product_name: str | None = None,
    place: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
    sort_by: FinanceSortField = FinanceSortField.DATE,
    sort_direction: SortDirection = SortDirection.DESC,
) -> FinanceQueryResult:
    """Query financial data using a structured mode and flat filters.

    ChatGPT must select the mode and provide filters from the user's natural
    language. This tool only queries PostgreSQL; it does not infer intent,
    classify products, or call an LLM.
    """
    request = FinanceQueryRequest(
        mode=mode,
        category=category,
        operation_type=operation_type,
        product_name=product_name,
        place=place,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )

    logger.info(
        "MCP tool called: tool=query_finance mode=%r category=%r "
        "operation_type=%r product_name=%r place=%r date_from=%r date_to=%r "
        "limit=%s sort_by=%r sort_direction=%r",
        mode,
        category,
        operation_type,
        product_name,
        place,
        date_from,
        date_to,
        limit,
        sort_by,
        sort_direction,
    )

    try:
        with SessionLocal() as session:
            logger.info(
                "MCP tool entered FinanceQueryService: tool=query_finance"
            )

            repository = FinanceRepository(session, get_default_user_id(session))
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
