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
- grocery

- drinks
- alcohol
- tobacco

- household
- medicine
- transport
- online_payments

- home
- salary
- part_time
- gift
- other

Choose the category based on the item's meaning.

Examples:

- milk -> dairy

- cheese -> dairy

- yogurt -> dairy

- eggs -> dairy
- chicken eggs -> dairy
- quail eggs -> dair

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

- ChatGPT -> online_payments

- Apple Music -> online_payments

- Netflix -> online_payments

- YouTube Premium -> online_payments

- OpenAI API -> online_payments

- mobile phone top-up -> online_payments

- home internet -> online_payments

- VPS hosting -> online_payments

- domain renewal -> online_payments

- apartment rent -> home

- utilities -> home

- electricity bill -> home

- water bill -> home

- heating -> home

- cement -> home

- putty -> home

- paint -> home

- electrical cable -> home

- wallpaper -> home

- furniture -> home

- monthly salary -> salary

- wages -> salary

- freelance work -> part_time

- side job -> part_time

- extra income -> part_time

- birthday money -> gift

- cash gift -> gift

- holiday gift -> gift

- electronics -> other

- stationery -> othe

- flour -> grocery
- wheat flour -> grocery
- rye flour -> grocery

- rice -> grocery
- buckwheat -> grocery
- oatmeal -> grocery
- pasta -> grocery

- lentils -> grocery
- chickpeas -> grocery
- beans -> grocery
- peas -> grocery

- dry yeast -> grocery
- fresh yeast -> grocery

- sugar -> grocery
- salt -> grocery

- sunflower oil -> grocery
- olive oil -> grocery

- ketchup -> grocery
- mayonnaise -> grocery
- soy sauce -> grocery
- tomato paste -> grocery

- spices -> grocery
- black pepper -> grocery
- bay leaf -> grocery

Grocery includes shelf-stable pantry foods such as flour, grains, pasta, legumes, sauces, cooking oil, sugar, salt, spices, and similar pantry products.

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
