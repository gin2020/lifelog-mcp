"""Интеграционные тесты режимов единого MCP-инструмента query_finance."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from app.db.database import SessionLocal
from app.core.dependencies import get_default_user_id
from app.db.models.finance_event import OperationType
from app.db.models.finance_item import FinanceCategory
from app.schemas.finance import FinanceEventCreate, FinanceItemCreate
from app.schemas.finance_query_request import (
    FinanceQueryMode,
    FinanceSortField,
    SortDirection,
)
from app.services.finance_service import FinanceService
from app.tools.query_finance import query_finance


class QueryFinanceToolTestCase(unittest.TestCase):
    """Проверяет все режимы query_finance на изолированных данных."""

    product_prefix = "qf-integration-"
    market = "qf-integration-market"
    other_market = "qf-integration-other"

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.finance_service = FinanceService(self.session)
        self.user_id = get_default_user_id(self.session)
        self.event_ids: list[int] = []
        self.date_from = datetime.now(timezone.utc) - timedelta(minutes=1)

        self._create_event(
            operation_type="expense",
            place=self.market,
            total_amount="350.00",
            item=FinanceItemCreate(
                name=f"{self.product_prefix}beef",
                category=FinanceCategory.MEAT,
                quantity=Decimal("1"),
                unit="kg",
                total_price=Decimal("350.00"),
            ),
        )
        self._create_event(
            operation_type="expense",
            place=self.market,
            total_amount="120.00",
            item=FinanceItemCreate(
                name=f"{self.product_prefix}milk",
                category=FinanceCategory.DAIRY,
                quantity=Decimal("1"),
                unit="pcs",
                total_price=Decimal("120.00"),
            ),
        )
        self._create_event(
            operation_type="expense",
            place=self.other_market,
            total_amount="90.00",
            item=FinanceItemCreate(
                name=f"{self.product_prefix}carrots",
                category=FinanceCategory.VEGETABLES,
                quantity=Decimal("1"),
                unit="kg",
                total_price=Decimal("90.00"),
            ),
        )
        self._create_event(
            operation_type="income",
            place="qf-integration-employer",
            total_amount="800.00",
            item=FinanceItemCreate(
                name=f"{self.product_prefix}bonus",
                category=FinanceCategory.OTHER,
                quantity=Decimal("1"),
                unit="pcs",
                total_price=Decimal("800.00"),
            ),
        )
        self.date_to = datetime.now(timezone.utc) + timedelta(minutes=1)

    def tearDown(self) -> None:
        for event_id in self.event_ids:
            self.finance_service.delete_event(self.user_id, event_id)
        self.session.close()

    def _create_event(
        self,
        *,
        operation_type: str,
        place: str,
        total_amount: str,
        item: FinanceItemCreate,
    ) -> None:
        event = self.finance_service.create_event(
            self.user_id,
            FinanceEventCreate(
                operation_type=operation_type,
                place=place,
                total_amount=Decimal(total_amount),
                items=[item],
            )
        )
        self.event_ids.append(event.id)

    def _query(self, **kwargs):
        return query_finance(
            product_name=self.product_prefix,
            date_from=self.date_from,
            date_to=self.date_to,
            **kwargs,
        )

    def test_total_amount_mode_keeps_legacy_answer(self) -> None:
        """Режим суммы возвращает структурированную сумму и прежнее поле answer."""
        result = self._query(category=FinanceCategory.MEAT)

        self.assertEqual(result.mode, FinanceQueryMode.TOTAL_AMOUNT)
        self.assertEqual(result.total, Decimal("350.00"))
        self.assertEqual(result.answer, "350.00 ₽")

    def test_transactions_mode_applies_limit_and_sorting(self) -> None:
        """История операций фильтруется, сортируется и ограничивается."""
        result = self._query(
            mode=FinanceQueryMode.TRANSACTIONS,
            place=self.market,
            limit=2,
            sort_by=FinanceSortField.AMOUNT,
            sort_direction=SortDirection.DESC,
        )

        self.assertEqual(result.mode, FinanceQueryMode.TRANSACTIONS)
        self.assertTrue(all(item.event_id > 0 for item in result.transactions))
        self.assertTrue(all(item.item_id > 0 for item in result.transactions))
        self.assertEqual([item.title for item in result.transactions], [
            f"{self.product_prefix}beef",
            f"{self.product_prefix}milk",
        ])

    def test_group_by_category_mode(self) -> None:
        """Группировка по категориям возвращает суммы всех расходов."""
        result = self._query(mode=FinanceQueryMode.GROUP_BY_CATEGORY)
        amounts = {item.category: item.amount for item in result.items}

        self.assertEqual(amounts[FinanceCategory.MEAT], Decimal("350.00"))
        self.assertEqual(amounts[FinanceCategory.DAIRY], Decimal("120.00"))
        self.assertEqual(amounts[FinanceCategory.VEGETABLES], Decimal("90.00"))

    def test_group_by_place_mode(self) -> None:
        """Группировка по месту показывает сумму и частоту покупок."""
        result = self._query(mode=FinanceQueryMode.GROUP_BY_PLACE)
        amounts = {item.place: item.amount for item in result.items}
        counts = {item.place: item.transaction_count for item in result.items}

        self.assertEqual(amounts[self.market], Decimal("470.00"))
        self.assertEqual(counts[self.market], 2)
        self.assertEqual(amounts[self.other_market], Decimal("90.00"))

    def test_statistics_mode(self) -> None:
        """Статистика отражает количество, сумму и границы цен."""
        result = self._query(mode=FinanceQueryMode.STATISTICS)

        self.assertIsNotNone(result.statistics)
        assert result.statistics is not None
        self.assertEqual(result.statistics.transaction_count, 3)
        self.assertEqual(result.statistics.total, Decimal("560.00"))
        self.assertEqual(result.statistics.min_amount, Decimal("90.00"))
        self.assertEqual(result.statistics.max_amount, Decimal("350.00"))

    def test_summary_mode_combines_statistics_and_categories(self) -> None:
        """Сводка объединяет общую сумму, статистику и категории."""
        result = self._query(mode=FinanceQueryMode.SUMMARY)

        self.assertEqual(result.total, Decimal("560.00"))
        self.assertIsNotNone(result.statistics)
        self.assertEqual({item.category for item in result.items}, {
            FinanceCategory.MEAT,
            FinanceCategory.DAIRY,
            FinanceCategory.VEGETABLES,
        })

    def test_income_filter(self) -> None:
        """Фильтр operation_type позволяет запрашивать доходы."""
        result = self._query(operation_type=OperationType.INCOME)

        self.assertEqual(result.total, Decimal("800.00"))
