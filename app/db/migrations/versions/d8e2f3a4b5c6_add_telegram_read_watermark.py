"""add separate Telegram user read watermark

Revision ID: d8e2f3a4b5c6
Revises: c7d1a2b3e4f5
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d8e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c7d1a2b3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telegram_dialogue_monitors", sa.Column("last_read_message_id", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("telegram_dialogue_monitors", "last_read_message_id")
