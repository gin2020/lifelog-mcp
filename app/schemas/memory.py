from pydantic import BaseModel, model_validator


class MemoryCreate(BaseModel):
    """Данные для создания воспоминания."""

    text: str
    memory_type: str


class MemoryUpdate(BaseModel):
    """Fields that may be changed on an existing memory."""

    text: str | None = None
    memory_type: str | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdate":
        """Reject an update that does not change anything."""
        if self.text is None and self.memory_type is None:
            raise ValueError("At least one memory field must be provided")
        return self
