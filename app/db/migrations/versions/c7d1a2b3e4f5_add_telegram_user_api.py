"""add per-user Telegram MTProto storage and allowlist

Revision ID: c7d1a2b3e4f5
Revises: bab02e72db55
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c7d1a2b3e4f5"
down_revision: str | Sequence[str] | None = "bab02e72db55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger()),
        sa.Column("username", sa.String(255)),
        sa.Column("display_name", sa.String(512)),
        sa.Column("session_ciphertext", sa.Text()),
        sa.Column("session_key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("telegram_user_id", name="uq_telegram_account_user_id"),
    )
    op.create_index("ix_telegram_accounts_user_id", "telegram_accounts", ["user_id"], unique=False)
    op.create_index("ix_telegram_accounts_status", "telegram_accounts", ["status"], unique=False)

    op.create_table(
        "telegram_auth_flows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("session_ciphertext", sa.Text()),
        sa.Column("status", sa.String(16), server_default=sa.text("'code_required'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_telegram_auth_flows_user_id", "telegram_auth_flows", ["user_id"], unique=False)
    op.create_index("ix_telegram_auth_flows_status", "telegram_auth_flows", ["status"], unique=False)
    op.create_index("ix_telegram_auth_flows_expires_at", "telegram_auth_flows", ["expires_at"], unique=False)

    op.create_table(
        "telegram_allowed_peers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("peer_type", sa.String(16), nullable=False),
        sa.Column("telegram_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("access_hash", sa.BigInteger()),
        sa.Column("username", sa.String(255)),
        sa.Column("display_name", sa.String(512)),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "peer_type", "telegram_peer_id", name="uq_telegram_allowed_peer"),
    )
    op.create_index("ix_telegram_allowed_peers_user_id", "telegram_allowed_peers", ["user_id"], unique=False)

    op.create_table(
        "telegram_dialogue_monitors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("allowed_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("monitor_kind", sa.String(16), server_default=sa.text("'new_messages'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("anchor_message_id", sa.BigInteger()),
        sa.Column("last_notified_message_id", sa.BigInteger()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["allowed_peer_id"], ["telegram_allowed_peers.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "allowed_peer_id", name="uq_telegram_dialogue_monitor"),
    )
    op.create_index("ix_telegram_dialogue_monitors_user_id", "telegram_dialogue_monitors", ["user_id"], unique=False)

    op.create_table(
        "telegram_send_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("allowed_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_peer_id", sa.BigInteger(), nullable=False),
        sa.Column("message_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["allowed_peer_id"], ["telegram_allowed_peers.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_telegram_send_requests_user_id", "telegram_send_requests", ["user_id"], unique=False)
    op.create_index("ix_telegram_send_requests_status", "telegram_send_requests", ["status"], unique=False)
    op.create_index("ix_telegram_send_requests_expires_at", "telegram_send_requests", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("telegram_send_requests")
    op.drop_table("telegram_dialogue_monitors")
    op.drop_table("telegram_allowed_peers")
    op.drop_table("telegram_auth_flows")
    op.drop_table("telegram_accounts")
