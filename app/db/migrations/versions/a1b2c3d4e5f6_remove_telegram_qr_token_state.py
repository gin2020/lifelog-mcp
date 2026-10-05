"""remove persisted Telegram QR token state

Revision ID: a1b2c3d4e5f6
Revises: f0a4b5c6d7e8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f0a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # QRLogin is process-local runtime state. Remove the legacy column that
    # could contain an encrypted token; tokens must never be persisted.
    op.drop_column("telegram_auth_flows", "qr_token_ciphertext")


def downgrade() -> None:
    op.add_column("telegram_auth_flows", sa.Column("qr_token_ciphertext", sa.Text()))
