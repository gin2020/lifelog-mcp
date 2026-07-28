"""Точка входа приложения: импорт инструментов и запуск MCP-сервера."""

from app.core.mcp import mcp
from app.tools import memory  # noqa: F401 - регистрация инструментов при импорте
from app.tools import remember  # noqa: F401 - регистрация инструментов при импорте
from app.tools import system  # noqa: F401 - регистрация инструментов при импорте
from app.tools import finance
from app.tools import query_finance

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
