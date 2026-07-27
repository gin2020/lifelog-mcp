"""Системные MCP-инструменты."""

import logging

from app.core.mcp import mcp


logger = logging.getLogger(__name__)


@mcp.tool()
def ping() -> str:
    """Проверка работы сервера."""
    logger.info("MCP tool called: tool=ping arguments={}")
    return "pong Даша 😘"
