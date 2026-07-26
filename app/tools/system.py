"""Системные MCP-инструменты."""

from app.core.mcp import mcp


@mcp.tool()
def ping() -> str:
    """Проверка работы сервера."""
    return "pong Даша 😘"
