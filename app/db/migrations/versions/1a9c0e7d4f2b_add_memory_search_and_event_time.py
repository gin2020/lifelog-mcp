"""add memory full-text search and event time

Revision ID: 1a9c0e7d4f2b
Revises: c0d1e2f3a4b5
Create Date: 2026-10-27 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "1a9c0e7d4f2b"
down_revision: str | Sequence[str] | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable event time and a GIN FTS index without rewriting memories.

    Existing rows retain ``NULL`` occurred_at because their event time is
    unknown.  The expression index is built from all existing text immediately,
    so search works as soon as this migration is applied.
    """
    op.add_column("memories", sa.Column("occurred_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_memories_user_occurred_at",
        "memories",
        ["user_id", "occurred_at"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_memories_search_vector ON memories USING gin "
        "(to_tsvector('simple'::regconfig, coalesce(text, '')))"
    )


def downgrade() -> None:
    """Remove the FTS index and the optional event timestamp."""
    op.execute("DROP INDEX ix_memories_search_vector")
    op.drop_index("ix_memories_user_occurred_at", table_name="memories")
    op.drop_column("memories", "occurred_at")
