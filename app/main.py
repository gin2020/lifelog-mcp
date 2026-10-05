"""Точка входа приложения: импорт инструментов и запуск MCP-сервера."""

from app.core.mcp import mcp
from app.core import auth_routes  # noqa: F401 - OAuth browser routes
from app.core import telegram_webhook_routes  # noqa: F401 - Telegram Bot webhook routes
from app.tools import memory  # noqa: F401 - регистрация инструментов при импорте
from app.tools import remember  # noqa: F401 - регистрация инструментов при импорте
from app.tools import system  # noqa: F401 - регистрация инструментов при импорте
from app.tools import finance
from app.tools import query_finance
from app.tools import telegram_user  # noqa: F401 - Telegram User API tools

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
