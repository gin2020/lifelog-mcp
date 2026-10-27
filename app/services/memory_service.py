"""Сервисный слой для работы с записями памяти."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from uuid import uuid4

from sqlalchemy import func, literal_column, select
from sqlalchemy.orm import Session

from app.db.models.memory import Memory
from app.schemas.memory import MemorySearchRequest, MemoryTimelineRequest, MemoryUpdate
from app.services.notification_outbox_service import NotificationOutboxService


logger = logging.getLogger(__name__)

# Keep the regconfig a SQL constant, rather than a bound value, so PostgreSQL
# can match the query expression to the identically configured GIN index.
_FTS_CONFIG = literal_column("'simple'::regconfig")


@dataclass(frozen=True)
class MemorySearchResult:
    """A memory together with its relevance score, when a search has one."""

    memory: Memory
    relevance: float | None = None


class MemoryService:
    """Provide ORM-backed operations for :class:`Memory` records."""

    def __init__(self, session: Session) -> None:
        """Initialize the service with an active SQLAlchemy session."""
        self._session = session

    def create_memory(
        self,
        user_id: int,
        text: str,
        memory_type: str,
        occurred_at: datetime | None = None,
    ) -> Memory:
        """Create and persist a new memory."""
        logger.info(
            "MemoryService.create_memory started: user_id=%s text=%r memory_type=%r",
            user_id,
            text,
            memory_type,
        )
        try:
            now = datetime.now(timezone.utc)
            memory = Memory(
                user_id=user_id,
                uuid=uuid4(),
                created_at=now,
                updated_at=now,
                occurred_at=occurred_at,
                text=text,
                memory_type=memory_type,
            )
            logger.info("MemoryService.create_memory ORM object created")
            self._session.add(memory)
            logger.info("MemoryService.create_memory session.add completed")
            self._session.flush()
            NotificationOutboxService(self._session).enqueue_created(
                user_id=user_id,
                aggregate_type="memory",
                aggregate_id=memory.id,
                event_type="memory.created",
            )
            self._session.commit()
            logger.info(
                "MemoryService.create_memory session.commit completed: memory_id=%s",
                memory.id,
            )
            self._session.refresh(memory)
            logger.info(
                "MemoryService.create_memory session.refresh completed: memory_id=%s",
                memory.id,
            )
            return memory
        except Exception:
            logger.exception("MemoryService.create_memory failed")
            try:
                self._session.rollback()
                logger.info("MemoryService.create_memory session.rollback completed")
            except Exception:
                logger.exception("MemoryService.create_memory session.rollback failed")
            raise

    def get_memory(
        self,
        user_id: int,
        memory_id: int,
    ) -> Memory | None:
        """Return a memory by its primary key, if it exists."""
        statement = select(Memory).where(
            Memory.id == memory_id,
            Memory.user_id == user_id,
        )
        return self._session.scalar(statement)

    def list_memories(
        self,
        user_id: int,
    ) -> list[Memory]:
        """Return all memories ordered by creation time."""
        statement = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.created_at, Memory.id)
        )
        return list(self._session.scalars(statement))

    def search_memories(
        self,
        user_id: int,
        request: MemorySearchRequest,
    ) -> list[MemorySearchResult]:
        """Search owned memories by text and return the most relevant first.

        PostgreSQL uses its indexed full-text search expression.  The small
        SQLite fallback exists solely for the isolated test suite; production
        PostgreSQL queries never interpolate user input into SQL.
        """
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            return self._postgresql_search(user_id, request)
        return self._portable_search(user_id, request)

    def memory_timeline(
        self,
        user_id: int,
        request: MemoryTimelineRequest,
    ) -> list[Memory]:
        """Return owned memories in chronological event-time order."""
        statement = select(Memory).where(
            Memory.user_id == user_id,
            Memory.occurred_at.is_not(None),
        )
        statement = self._apply_filters(
            statement,
            memory_type=request.memory_type,
            occurred_from=request.occurred_from,
            occurred_to=request.occurred_to,
        )
        statement = statement.order_by(Memory.occurred_at.asc(), Memory.id.asc()).limit(
            request.limit
        )
        return list(self._session.scalars(statement))

    def delete_memory(
        self,
        user_id: int,
        memory_id: int,
    ) -> bool:
        """Delete a memory and return whether it existed."""
        memory = self.get_memory(user_id, memory_id)
        if memory is None:
            return False

        try:
            self._session.delete(memory)
            NotificationOutboxService(self._session).enqueue_deleted(
                user_id=user_id,
                aggregate_type="memory",
                aggregate_id=memory_id,
                event_type="memory.deleted",
            )
            self._session.commit()
            return True
        except Exception:
            self._session.rollback()
            raise

    def update_memory(
        self,
        user_id: int,
        memory_id: int,
        changes: MemoryUpdate,
    ) -> Memory | None:
        """Update one owned memory and return it, if it exists."""
        memory = self.get_memory(user_id, memory_id)
        if memory is None:
            return None

        if changes.text is not None:
            memory.text = changes.text
        if changes.memory_type is not None:
            memory.memory_type = changes.memory_type
        if "occurred_at" in changes.model_fields_set:
            memory.occurred_at = changes.occurred_at
        memory.updated_at = datetime.now(timezone.utc)

        try:
            NotificationOutboxService(self._session).enqueue_updated(
                user_id=user_id,
                aggregate_type="memory",
                aggregate_id=memory.id,
                event_type="memory.updated",
            )
            self._session.commit()
            self._session.refresh(memory)
            return memory
        except Exception:
            self._session.rollback()
            raise

    def _postgresql_search(
        self,
        user_id: int,
        request: MemorySearchRequest,
    ) -> list[MemorySearchResult]:
        """Run an indexed PostgreSQL FTS query with bound parameters."""
        vector = func.to_tsvector(_FTS_CONFIG, func.coalesce(Memory.text, ""))
        tsquery = func.websearch_to_tsquery(_FTS_CONFIG, request.query)
        relevance = func.ts_rank_cd(vector, tsquery).label("relevance")
        statement = select(Memory, relevance).where(
            Memory.user_id == user_id,
            vector.op("@@")(tsquery),
        )
        statement = self._apply_filters(
            statement,
            memory_type=request.memory_type,
            occurred_from=request.occurred_from,
            occurred_to=request.occurred_to,
        )
        statement = statement.order_by(
            relevance.desc(),
            Memory.occurred_at.desc().nullslast(),
            Memory.created_at.desc().nullslast(),
            Memory.id.desc(),
        ).limit(request.limit)
        return [
            MemorySearchResult(memory=memory, relevance=float(rank))
            for memory, rank in self._session.execute(statement)
        ]

    def _portable_search(
        self,
        user_id: int,
        request: MemorySearchRequest,
    ) -> list[MemorySearchResult]:
        """Deterministic non-PostgreSQL fallback used by local SQLite tests."""
        statement = select(Memory).where(Memory.user_id == user_id)
        statement = self._apply_filters(
            statement,
            memory_type=request.memory_type,
            occurred_from=request.occurred_from,
            occurred_to=request.occurred_to,
        )
        terms = re.findall(r"\w+", request.query.lower(), flags=re.UNICODE)
        results: list[MemorySearchResult] = []
        for memory in self._session.scalars(statement):
            text = memory.text.lower()
            score = sum(text.count(term) for term in terms)
            if score:
                results.append(MemorySearchResult(memory=memory, relevance=float(score)))
        results.sort(
            key=lambda result: (
                -(result.relevance or 0),
                -self._timestamp(result.memory.occurred_at),
                -self._timestamp(result.memory.created_at),
                -result.memory.id,
            ),
        )
        return results[: request.limit]

    @staticmethod
    def _apply_filters(statement, *, memory_type, occurred_from, occurred_to):
        """Add only bound, deterministic filters shared by reads."""
        if memory_type is not None:
            statement = statement.where(Memory.memory_type == memory_type)
        if occurred_from is not None:
            statement = statement.where(Memory.occurred_at >= occurred_from)
        if occurred_to is not None:
            statement = statement.where(Memory.occurred_at <= occurred_to)
        return statement

    @staticmethod
    def _timestamp(value: datetime | None) -> float:
        """Produce a comparable sort value for SQLite and PostgreSQL datetimes."""
        return value.timestamp() if value is not None else 0.0
