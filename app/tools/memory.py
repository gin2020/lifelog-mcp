"""MCP-инструменты для работы с записями памяти."""

from app.core.mcp import mcp
from app.db.database import SessionLocal
from app.services.memory_service import MemoryService


@mcp.tool()
def create_memory(text: str, memory_type: str) -> dict[str, int | str]:
    """Create a memory record with text and a memory type."""
    with SessionLocal() as session:
        memory = MemoryService(session).create_memory(text, memory_type)

    return {
        "id": memory.id,
        "uuid": str(memory.uuid),
        "status": "created",
    }
