"""persist encrypted Telegram phone code hash for staged auth

Revision ID: e9f3a4b5c6d7
Revises: d8e2f3a4b5c6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e9f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d8e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telegram_auth_flows", sa.Column("phone_code_hash_ciphertext", sa.Text()))


def downgrade() -> None:
    op.drop_column("telegram_auth_flows", "phone_code_hash_ciphertext")
