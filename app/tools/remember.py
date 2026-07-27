"""Высокоуровневый MCP-инструмент для сохранения воспоминаний."""

import logging

from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.services.memory_service import MemoryService


logger = logging.getLogger(__name__)


@mcp.tool()
def remember(text: str, memory_type: str) -> dict[str, int | str]:
    """Store general memories only.

Use this tool for:
- facts
- events
- thoughts
- ideas
- observations

Do NOT use this tool for:
- purchases
- expenses
- income
- prices
- receipts
- financial transactions

Financial operations must be stored using create_finance_event.
The text and memory_type must already be determined before calling this tool.
"""
    logger.info(
        "MCP tool called: tool=remember text=%r memory_type=%r",
        text,
        memory_type,
    )
    try:
        with SessionLocal() as session:
            logger.info("MCP tool entered MemoryService: tool=remember")
            memory = MemoryService(session).create_memory(text, memory_type)
    except Exception:
        logger.exception("MCP tool failed: tool=remember")
        raise

    logger.info(
        "MCP tool succeeded: tool=remember memory_id=%s memory_uuid=%s",
        memory.id,
        memory.uuid,
    )

    return {
        "id": memory.id,
        "uuid": str(memory.uuid),
        "status": "created",
    }
