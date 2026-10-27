"""Memory search, timeline, limits, and user-isolation checks."""

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.db.database import SessionLocal
from app.db.models.notification import NotificationOutbox
from app.db.models.user import User
from app.schemas.memory import MemorySearchRequest, MemoryTimelineRequest
from app.services.memory_service import MemoryService
from app.tools.memory import get_memory, memory_timeline, search_memories


UTC = timezone.utc


class MemorySearchTestCase(unittest.TestCase):
    """Checks memory reads without requiring a PostgreSQL test server."""

    def setUp(self) -> None:
        self.session = SessionLocal()
        self.service = MemoryService(self.session)
        self.owner = User()
        self.other_user = User()
        self.session.add_all([self.owner, self.other_user])
        self.session.commit()
        self.memory_ids: list[int] = []

    def tearDown(self) -> None:
        for memory_id in self.memory_ids:
            self.service.delete_memory(self.owner.id, memory_id)
            self.service.delete_memory(self.other_user.id, memory_id)
        self.session.execute(
            NotificationOutbox.__table__.delete().where(
                NotificationOutbox.user_id.in_([self.owner.id, self.other_user.id])
            )
        )
        self.session.delete(self.owner)
        self.session.delete(self.other_user)
        self.session.commit()
        self.session.close()

    def _create(
        self,
        text: str,
        memory_type: str = "note",
        occurred_at: datetime | None = None,
        *,
        user_id: int | None = None,
    ):
        memory = self.service.create_memory(
            user_id or self.owner.id, text, memory_type, occurred_at
        )
        self.memory_ids.append(memory.id)
        return memory

    def test_search_matches_words_and_ranks_by_relevance(self) -> None:
        high = self._create("Milk milk for breakfast")
        low = self._create("Buy milk on the way home")
        self._create("Call the dentist")

        results = self.service.search_memories(
            self.owner.id, MemorySearchRequest(query="milk")
        )

        self.assertEqual([item.memory.id for item in results], [high.id, low.id])
        self.assertGreater(results[0].relevance or 0, results[1].relevance or 0)

    def test_search_filters_by_type_and_event_date_not_save_date(self) -> None:
        jan = self._create(
            "Milk delivery arrived", "event", datetime(2026, 1, 15, tzinfo=UTC)
        )
        self._create(
            "Milk shopping list", "note", datetime(2026, 2, 15, tzinfo=UTC)
        )
        undated = self._create("Milk without known event date", "event")

        results = self.service.search_memories(
            self.owner.id,
            MemorySearchRequest(
                query="milk",
                memory_type="event",
                occurred_from=datetime(2026, 1, 1, tzinfo=UTC),
                occurred_to=datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
            ),
        )

        self.assertEqual([item.memory.id for item in results], [jan.id])
        self.assertNotIn(undated.id, [item.memory.id for item in results])

    def test_empty_result_and_limit(self) -> None:
        for number in range(3):
            self._create(f"project roadmap item {number}")

        self.assertEqual(
            self.service.search_memories(
                self.owner.id, MemorySearchRequest(query="unmatched")
            ),
            [],
        )
        limited = self.service.search_memories(
            self.owner.id, MemorySearchRequest(query="project", limit=2)
        )
        self.assertEqual(len(limited), 2)

    def test_timeline_is_chronological_and_skips_unknown_event_dates(self) -> None:
        second = self._create("second", occurred_at=datetime(2026, 1, 2, tzinfo=UTC))
        first = self._create("first", occurred_at=datetime(2026, 1, 1, tzinfo=UTC))
        self._create("undated")

        timeline = self.service.memory_timeline(
            self.owner.id,
            MemoryTimelineRequest(
                occurred_from=datetime(2026, 1, 1, tzinfo=UTC),
                occurred_to=datetime(2026, 1, 31, tzinfo=UTC),
            ),
        )

        self.assertEqual([memory.id for memory in timeline], [first.id, second.id])

    def test_read_tools_do_not_expose_another_users_memory(self) -> None:
        private = self._create(
            "private launch plan",
            "event",
            datetime(2026, 3, 1, tzinfo=UTC),
            user_id=self.other_user.id,
        )

        with patch("app.tools.memory.get_request_user_id", return_value=self.owner.id):
            self.assertEqual(get_memory(private.id)["status"], "not_found")
            self.assertEqual(search_memories("launch")["memories"], [])
            self.assertEqual(
                memory_timeline(
                    occurred_from=datetime(2026, 3, 1, tzinfo=UTC),
                    occurred_to=datetime(2026, 3, 2, tzinfo=UTC),
                )["memories"],
                [],
            )

    def test_invalid_search_and_timeline_requests_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MemorySearchRequest(query="   ")
        with self.assertRaises(ValidationError):
            MemorySearchRequest(query="ok", limit=0)
        with self.assertRaises(ValidationError):
            MemorySearchRequest(
                query="ok",
                occurred_from=datetime(2026, 2, 1, tzinfo=UTC),
                occurred_to=datetime(2026, 1, 1, tzinfo=UTC),
            )
        with self.assertRaises(ValidationError):
            MemoryTimelineRequest()
