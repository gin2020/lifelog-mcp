"""Сервисный слой для работы с записями памяти."""

from datetime import datetime, timezone
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.memory import Memory
from app.services.notification_outbox_service import NotificationOutboxService


logger = logging.getLogger(__name__)


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

    def delete_memory(
        self,
        user_id: int,
        memory_id: int,
    ) -> bool:
        """Delete a memory and return whether it existed."""
        memory = self.get_memory(user_id, memory_id)
        if memory is None:
            return False

        self._session.delete(memory)
        self._session.commit()
        return True
