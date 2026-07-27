from pydantic import BaseModel


class MemoryCreate(BaseModel):
    """Данные для создания воспоминания."""

    text: str
    memory_type: str
