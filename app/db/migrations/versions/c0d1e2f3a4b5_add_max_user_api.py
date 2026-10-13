"""add personal MAX user-client integration"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c0d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "max_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(64), server_default=sa.text("'legacy'"), nullable=False),
        sa.Column("max_user_id", sa.BigInteger(), nullable=True),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("display_name", sa.String(512), nullable=True),
        sa.Column("session_ciphertext", sa.Text(), nullable=True),
        sa.Column("session_key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "label", name="uq_max_account_user_label"),
        sa.UniqueConstraint("max_user_id", name="uq_max_account_max_user_id"),
    )
    op.create_index("ix_max_accounts_user_id", "max_accounts", ["user_id"], unique=False)
    op.create_index("ix_max_accounts_status", "max_accounts", ["status"], unique=False)

    op.create_table(
        "max_auth_flows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=True),
        sa.Column("account_label", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'qr_code_required'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["max_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_max_auth_flows_user_id", "max_auth_flows", ["user_id"], unique=False)
    op.create_index("ix_max_auth_flows_account_id", "max_auth_flows", ["account_id"], unique=False)
    op.create_index("ix_max_auth_flows_status", "max_auth_flows", ["status"], unique=False)
    op.create_index("ix_max_auth_flows_expires_at", "max_auth_flows", ["expires_at"], unique=False)

    op.create_table(
        "max_allowed_chats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("max_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=True),
        sa.Column("chat_type", sa.String(32), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["max_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "max_chat_id", name="uq_max_allowed_chat"),
    )
    op.create_index("ix_max_allowed_chats_user_id", "max_allowed_chats", ["user_id"], unique=False)
    op.create_index("ix_max_allowed_chats_account_id", "max_allowed_chats", ["account_id"], unique=False)

    op.create_table(
        "max_chat_monitors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("allowed_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("monitor_kind", sa.String(16), server_default=sa.text("'new_messages'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("anchor_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_notified_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_read_message_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["max_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["allowed_chat_id"], ["max_allowed_chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "allowed_chat_id", name="uq_max_chat_monitor"),
    )
    op.create_index("ix_max_chat_monitors_user_id", "max_chat_monitors", ["user_id"], unique=False)
    op.create_index("ix_max_chat_monitors_account_id", "max_chat_monitors", ["account_id"], unique=False)

    op.create_table(
        "max_send_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("allowed_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("max_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_ciphertext", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["max_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["allowed_chat_id"], ["max_allowed_chats.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_max_send_requests_user_id", "max_send_requests", ["user_id"], unique=False)
    op.create_index("ix_max_send_requests_account_id", "max_send_requests", ["account_id"], unique=False)
    op.create_index("ix_max_send_requests_status", "max_send_requests", ["status"], unique=False)
    op.create_index("ix_max_send_requests_expires_at", "max_send_requests", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("max_send_requests")
    op.drop_table("max_chat_monitors")
    op.drop_table("max_allowed_chats")
    op.drop_table("max_auth_flows")
    op.drop_table("max_accounts")
