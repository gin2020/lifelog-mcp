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

Category must be one of these values only:

- dairy

- meat

- vegetables

- fruits

- drinks
- alcohol
- tobacco

- household
- medicine
- transport
- other

Choose the category based on the item's meaning.

Examples:

- milk -> dairy

- cheese -> dairy

- yogurt -> dairy

- chicken breast -> meat

- beef -> meat

- fish -> meat

- tomatoes -> vegetables

- potatoes -> vegetables

- onions -> vegetables

- apples -> fruits

- bananas -> fruits

- oranges -> fruits

- coffee -> drinks

- tea -> drinks

- water -> drinks

- juice -> drinks

- energy drink -> drinks

- beer -> alcohol

- wine -> alcohol

- vodka -> alcohol

- whiskey -> alcohol

- cigarettes -> tobacco

- tobacco -> tobacco

- cigars -> tobacco

- laundry detergent -> household

- toilet paper -> household

- dish soap -> household

- medicine -> medicine

- vitamins -> medicine

- painkillers -> medicine

- taxi -> transport

- bus ticket -> transport

- gasoline -> transport

- parking -> transport

- gift -> other

- electronics -> other

- stationery -> othe

Never invent new category names.

Never use localized category names such as "Продукты", "Напитки" or similar.

Use only the enum values listed above.

operation_type:
- expense
- income

currency:
- RUB

quantity:
Default to 1 if the user does not specify it.

unit:
Default to "pcs" for countable items.
Use "kg", "g", "l", "ml" when appropriate.

total_amount:
Sum of all item prices.

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
