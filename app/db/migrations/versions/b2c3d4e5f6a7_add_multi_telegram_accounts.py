"""add labelled Telegram accounts and account-scoped user data

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telegram_accounts", sa.Column("label", sa.String(64), nullable=True))
    op.execute("UPDATE telegram_accounts SET label = 'legacy' WHERE label IS NULL")
    op.alter_column("telegram_accounts", "label", nullable=False)
    # ``unique=True`` on the original model created this database constraint.
    op.drop_constraint("telegram_accounts_user_id_key", "telegram_accounts", type_="unique")
    op.create_unique_constraint("uq_telegram_account_user_label", "telegram_accounts", ["user_id", "label"])

    op.add_column("telegram_auth_flows", sa.Column("account_id", sa.BigInteger(), nullable=True))
    op.add_column("telegram_auth_flows", sa.Column("account_label", sa.String(64), nullable=True))
    op.create_foreign_key("fk_telegram_auth_flows_account_id", "telegram_auth_flows", "telegram_accounts", ["account_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_telegram_auth_flows_account_id", "telegram_auth_flows", ["account_id"], unique=False)

    for table in ("telegram_allowed_peers", "telegram_dialogue_monitors", "telegram_send_requests"):
        op.add_column(table, sa.Column("account_id", sa.BigInteger(), nullable=True))
        op.create_index(f"ix_{table}_account_id", table, ["account_id"], unique=False)
        op.create_foreign_key(f"fk_{table}_account_id", table, "telegram_accounts", ["account_id"], ["id"], ondelete="CASCADE" if table != "telegram_send_requests" else "RESTRICT")

    # Existing installations have at most one account per user. Resolve all
    # legacy rows before making the new account scope mandatory.
    op.execute("""
        UPDATE telegram_allowed_peers p
        SET account_id = a.id
        FROM telegram_accounts a
        WHERE p.user_id = a.user_id AND p.account_id IS NULL
    """)
    op.execute("""
        UPDATE telegram_dialogue_monitors m
        SET account_id = p.account_id
        FROM telegram_allowed_peers p
        WHERE m.allowed_peer_id = p.id AND m.account_id IS NULL
    """)
    op.execute("""
        UPDATE telegram_send_requests r
        SET account_id = p.account_id
        FROM telegram_allowed_peers p
        WHERE r.allowed_peer_id = p.id AND r.account_id IS NULL
    """)

    op.drop_constraint("uq_telegram_allowed_peer", "telegram_allowed_peers", type_="unique")
    op.create_unique_constraint("uq_telegram_allowed_peer", "telegram_allowed_peers", ["account_id", "peer_type", "telegram_peer_id"])
    op.drop_constraint("uq_telegram_dialogue_monitor", "telegram_dialogue_monitors", type_="unique")
    op.create_unique_constraint("uq_telegram_dialogue_monitor", "telegram_dialogue_monitors", ["account_id", "allowed_peer_id"])

    for table in ("telegram_allowed_peers", "telegram_dialogue_monitors", "telegram_send_requests"):
        op.alter_column(table, "account_id", nullable=False)


def downgrade() -> None:
    for table in ("telegram_send_requests", "telegram_dialogue_monitors", "telegram_allowed_peers"):
        op.drop_constraint(f"fk_{table}_account_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_account_id", table_name=table)
        op.drop_column(table, "account_id")
    op.drop_index("ix_telegram_auth_flows_account_id", table_name="telegram_auth_flows")
    op.drop_constraint("fk_telegram_auth_flows_account_id", "telegram_auth_flows", type_="foreignkey")
    op.drop_column("telegram_auth_flows", "account_label")
    op.drop_column("telegram_auth_flows", "account_id")
    op.drop_constraint("uq_telegram_account_user_label", "telegram_accounts", type_="unique")
    op.create_unique_constraint("telegram_accounts_user_id_key", "telegram_accounts", ["user_id"])
    op.drop_column("telegram_accounts", "label")
