"""MCP-инструменты для работы с записями памяти."""

from datetime import datetime
import logging
from typing import Annotated

from pydantic import Field

from app.core.mcp import mcp
from app.core.dependencies import get_request_user_id
from app.db.database import SessionLocal
from app.schemas.memory import (
    MAX_MEMORY_RESULTS,
    MAX_MEMORY_SEARCH_QUERY_LENGTH,
    MemorySearchRequest,
    MemoryTimelineRequest,
    MemoryUpdate,
)
from app.services.memory_service import MemorySearchResult, MemoryService


logger = logging.getLogger(__name__)


@mcp.tool()
def create_memory(
    text: str,
    memory_type: str,
    occurred_at: datetime | None = None,
) -> dict[str, int | str]:
    """Create a memory record.

    ``occurred_at`` is optional event/fact time.  ``created_at`` is always the
    server save time and is never inferred as an event date.
    """
    logger.info(
        "MCP tool called: tool=create_memory text=%r memory_type=%r",
        text,
        memory_type,
    )
    try:
        with SessionLocal() as session:
            logger.info("MCP tool entered MemoryService: tool=create_memory")
            memory = MemoryService(session).create_memory(
                get_request_user_id(session), text, memory_type, occurred_at
            )
    except Exception:
        logger.exception("MCP tool failed: tool=create_memory")
        raise

    logger.info(
        "MCP tool succeeded: tool=create_memory memory_id=%s memory_uuid=%s",
        memory.id,
        memory.uuid,
    )

    return {
        "id": memory.id,
        "uuid": str(memory.uuid),
        "status": "created",
    }


@mcp.tool()
def update_memory(
    memory_id: int,
    changes: MemoryUpdate,
) -> dict[str, int | str]:
    """Update an owned memory record."""
    with SessionLocal() as session:
        memory = MemoryService(session).update_memory(
            get_request_user_id(session), memory_id, changes
        )

    if memory is None:
        return {"id": memory_id, "status": "not_found"}

    return {
        "id": memory.id,
        "uuid": str(memory.uuid),
        "status": "updated",
    }


@mcp.tool()
def delete_memory(memory_id: int) -> dict[str, int | str]:
    """Delete an owned memory record."""
    with SessionLocal() as session:
        deleted = MemoryService(session).delete_memory(
            get_request_user_id(session), memory_id
        )

    return {
        "id": memory_id,
        "status": "deleted" if deleted else "not_found",
    }


@mcp.tool()
def get_memory(
    memory_id: Annotated[int, Field(gt=0)],
) -> dict[str, object]:
    """Return one memory by ID, only when it belongs to the request user."""
    with SessionLocal() as session:
        memory = MemoryService(session).get_memory(
            get_request_user_id(session), memory_id
        )
    if memory is None:
        return {"id": memory_id, "status": "not_found"}
    return {"status": "found", "memory": _serialize_memory(memory, text_limit=8_000)}


@mcp.tool()
def search_memories(
    query: Annotated[str, Field(min_length=1, max_length=MAX_MEMORY_SEARCH_QUERY_LENGTH)],
    memory_type: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_MEMORY_RESULTS)] = 20,
) -> dict[str, object]:
    """Find the request user's memories by natural-language text and filters.

    PostgreSQL full-text search ranks results by relevance.  Date filters use
    ``occurred_at`` (when the fact/event happened), not ``created_at``.
    """
    request = MemorySearchRequest(
        query=query,
        memory_type=memory_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
    )
    with SessionLocal() as session:
        results = MemoryService(session).search_memories(
            get_request_user_id(session), request
        )
    return {
        "query": request.query,
        "count": len(results),
        "memories": [_serialize_search_result(result) for result in results],
    }


@mcp.tool()
def memory_timeline(
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    memory_type: str | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_MEMORY_RESULTS)] = 20,
) -> dict[str, object]:
    """List the request user's dated memories chronologically for a period.

    A boundary is required.  Memories without ``occurred_at`` have no known
    event date and therefore do not appear in a date-based timeline.
    """
    request = MemoryTimelineRequest(
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        memory_type=memory_type,
        limit=limit,
    )
    with SessionLocal() as session:
        memories = MemoryService(session).memory_timeline(
            get_request_user_id(session), request
        )
    return {
        "count": len(memories),
        "memories": [_serialize_memory(memory) for memory in memories],
    }


def _serialize_search_result(result: MemorySearchResult) -> dict[str, object]:
    """Serialize a search row without exposing ORM or datetime values."""
    payload = _serialize_memory(result.memory)
    payload["relevance"] = result.relevance
    return payload


def _serialize_memory(memory, *, text_limit: int = 2_000) -> dict[str, object]:
    """Keep bounded list responses while preserving useful record metadata."""
    text = memory.text
    return {
        "id": memory.id,
        "uuid": str(memory.uuid),
        "text": text[:text_limit],
        "text_truncated": len(text) > text_limit,
        "memory_type": memory.memory_type,
        "occurred_at": memory.occurred_at.isoformat() if memory.occurred_at else None,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
    }
