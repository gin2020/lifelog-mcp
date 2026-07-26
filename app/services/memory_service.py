"""Сервисный слой для работы с записями памяти."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.memory import Memory


class MemoryService:
    """Provide ORM-backed operations for :class:`Memory` records."""

    def __init__(self, session: Session) -> None:
        """Initialize the service with an active SQLAlchemy session."""
        self._session = session

    def create_memory(
        self,
        text: str,
        memory_type: str,
    ) -> Memory:
        """Create and persist a new memory."""
        now = datetime.now(timezone.utc)
        memory = Memory(
            uuid=uuid4(),
            created_at=now,
            updated_at=now,
            text=text,
            memory_type=memory_type,
        )
        self._session.add(memory)
        self._session.commit()
        self._session.refresh(memory)
        return memory

    def get_memory(
        self,
        memory_id: int,
    ) -> Memory | None:
        """Return a memory by its primary key, if it exists."""
        return self._session.get(Memory, memory_id)

    def list_memories(
        self,
    ) -> list[Memory]:
        """Return all memories ordered by creation time."""
        statement = select(Memory).order_by(Memory.created_at, Memory.id)
        return list(self._session.scalars(statement))

    def delete_memory(
        self,
        memory_id: int,
    ) -> bool:
        """Delete a memory and return whether it existed."""
        memory = self.get_memory(memory_id)
        if memory is None:
            return False

        self._session.delete(memory)
        self._session.commit()
        return True
