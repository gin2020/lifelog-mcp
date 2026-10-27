from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


MAX_MEMORY_SEARCH_QUERY_LENGTH = 500
MAX_MEMORY_RESULTS = 50


class MemoryCreate(BaseModel):
    """Данные для создания воспоминания."""

    text: str
    memory_type: str
    occurred_at: datetime | None = None


class MemoryUpdate(BaseModel):
    """Fields that may be changed on an existing memory."""

    text: str | None = None
    memory_type: str | None = None
    occurred_at: datetime | None = None

    @model_validator(mode="after")
    def require_change(self) -> "MemoryUpdate":
        """Reject an update that does not change anything."""
        if not self.model_fields_set:
            raise ValueError("At least one memory field must be provided")
        if "text" in self.model_fields_set and self.text is None:
            raise ValueError("text cannot be null")
        if "memory_type" in self.model_fields_set and self.memory_type is None:
            raise ValueError("memory_type cannot be null")
        return self


class MemorySearchRequest(BaseModel):
    """Validated filters for relevance-ranked memory search.

    ``occurred_from`` and ``occurred_to`` apply to the time of the remembered
    event/fact, never to the time when the record was saved.
    """

    query: str = Field(min_length=1, max_length=MAX_MEMORY_SEARCH_QUERY_LENGTH)
    memory_type: str | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    limit: int = Field(default=20, ge=1, le=MAX_MEMORY_RESULTS)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be empty")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "MemorySearchRequest":
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("occurred_from must be before or equal to occurred_to")
        return self


class MemoryTimelineRequest(BaseModel):
    """Validated request for chronological memory retrieval."""

    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    memory_type: str | None = None
    limit: int = Field(default=20, ge=1, le=MAX_MEMORY_RESULTS)

    @model_validator(mode="after")
    def validate_period(self) -> "MemoryTimelineRequest":
        if self.occurred_from is None and self.occurred_to is None:
            raise ValueError("at least one timeline boundary must be provided")
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("occurred_from must be before or equal to occurred_to")
        return self
