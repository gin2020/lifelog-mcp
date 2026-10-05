"""add encrypted Telegram QR auth state

Revision ID: f0a4b5c6d7e8
Revises: e9f3a4b5c6d7
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f0a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e9f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("telegram_auth_flows", "phone", nullable=True)
    op.add_column("telegram_auth_flows", sa.Column("qr_token_ciphertext", sa.Text()))
    op.add_column("telegram_auth_flows", sa.Column("qr_expires_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("telegram_auth_flows", "qr_expires_at")
    op.drop_column("telegram_auth_flows", "qr_token_ciphertext")
    op.alter_column("telegram_auth_flows", "phone", nullable=False)
