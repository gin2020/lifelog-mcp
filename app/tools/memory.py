"""MCP-инструменты для работы с записями памяти."""

import logging

from app.core.mcp import mcp
from app.core.dependencies import get_request_user_id
from app.db.database import SessionLocal
from app.schemas.memory import MemoryUpdate
from app.services.memory_service import MemoryService


logger = logging.getLogger(__name__)


@mcp.tool()
def create_memory(text: str, memory_type: str) -> dict[str, int | str]:
    """Create a memory record with text and a memory type."""
    logger.info(
        "MCP tool called: tool=create_memory text=%r memory_type=%r",
        text,
        memory_type,
    )
    try:
        with SessionLocal() as session:
            logger.info("MCP tool entered MemoryService: tool=create_memory")
            memory = MemoryService(session).create_memory(
                get_request_user_id(session), text, memory_type
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
