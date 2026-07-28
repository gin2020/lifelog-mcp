"""Интеграционная проверка плоского MCP-контракта query_finance."""

import unittest

from app.db.models.finance_item import FinanceCategory
from app.tools.query_finance import query_finance


class QueryFinanceToolTestCase(unittest.TestCase):
    """Проверяет запрос суммы расходов по категории без вложенного request."""

    def test_query_total_expenses_for_meat(self) -> None:
        """Tool принимает плоский category и возвращает ответ из PostgreSQL."""
        result = query_finance(category=FinanceCategory.MEAT)

        self.assertIsInstance(result.answer, str)
        self.assertTrue(result.answer.endswith(" ₽"))
