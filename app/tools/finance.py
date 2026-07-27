"""MCP-инструменты для работы с финансовыми событиями."""

import logging

from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.schemas.finance import FinanceEventCreate
from app.services.finance_service import FinanceService


logger = logging.getLogger(__name__)


@mcp.tool()
def create_finance_event(
    event: FinanceEventCreate,
) -> dict[str, int | str]:
    """Record structured financial transactions.

Always use this tool when the user mentions:
- buying something
- purchasing goods
- spending money
- paying for services
- selling something
- receiving income
- prices
- receipts
- financial operations

Extract all purchased items from natural language.

Examples:
- "Bought milk for 120 rubles and eggs for 87."
- "Spent 500 on fuel."
- "Received salary of 70000."
- "Sold my bicycle for 15000."

Do not use the remember tool for financial operations.
"""
    logger.info(
        "MCP tool called: tool=create_finance_event operation_type=%r total_amount=%s",
        event.operation_type,
        event.total_amount,
    )

    try:
        with SessionLocal() as session:
            logger.info(
                "MCP tool entered FinanceService: tool=create_finance_event"
            )

            finance_event = FinanceService(session).create_event(event)

    except Exception:
        logger.exception(
            "MCP tool failed: tool=create_finance_event"
        )
        raise

    logger.info(
        "MCP tool succeeded: tool=create_finance_event id=%s uuid=%s",
        finance_event.id,
        finance_event.uuid,
    )

    return {
        "id": finance_event.id,
        "uuid": str(finance_event.uuid),
        "status": "created",
    }
