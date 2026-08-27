"""add multi user ownership

Revision ID: 71a6d7a9b8f8
Revises: eca1929127fd
Create Date: 2026-08-18 17:00:28.720124

"""
from collections.abc import Sequence
from uuid import uuid4

from alembic import op
from alembic import context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71a6d7a9b8f8'
down_revision: str | Sequence[str] | None = 'eca1929127fd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user ownership and assign existing data to the configured owner."""
    telegram_id = context.get_x_argument(as_dictionary=True).get(
        "default_user_telegram_id"
    )
    if not telegram_id or not telegram_id.isdecimal():
        raise RuntimeError(
            "Pass the existing owner's numeric Telegram ID with "
            "-x default_user_telegram_id=<id>."
        )

    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('uuid', sa.Uuid(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('uuid')
    )
    op.create_table('user_identities',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('provider', sa.String(length=64), nullable=False),
    sa.Column('provider_subject', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_subject', name='uq_user_identity_provider_subject')
    )
    op.create_index(op.f('ix_user_identities_user_id'), 'user_identities', ['user_id'], unique=False)
    users = sa.table(
        "users",
        sa.column("id", sa.BigInteger),
        sa.column("uuid", sa.Uuid),
        sa.column("is_active", sa.Boolean),
        sa.column("token_version", sa.BigInteger),
    )
    user_identities = sa.table(
        "user_identities",
        sa.column("user_id", sa.BigInteger),
        sa.column("provider", sa.String),
        sa.column("provider_subject", sa.String),
    )
    memories = sa.table("memories", sa.column("user_id", sa.BigInteger))
    finance_events = sa.table(
        "finance_events", sa.column("user_id", sa.BigInteger)
    )
    connection = op.get_bind()
    owner_id = connection.execute(
        sa.insert(users)
        .values(
            uuid=uuid4(),
            is_active=True,
            token_version=0,
        )
        .returning(users.c.id)
    ).scalar_one()
    connection.execute(
        sa.insert(user_identities).values(
            user_id=owner_id,
            provider="telegram",
            provider_subject=telegram_id,
        )
    )

    op.add_column('finance_events', sa.Column('user_id', sa.BigInteger(), nullable=True))
    connection.execute(sa.update(finance_events).values(user_id=owner_id))
    op.alter_column('finance_events', 'user_id', nullable=False)
    op.create_index(op.f('ix_finance_events_user_id'), 'finance_events', ['user_id'], unique=False)
    op.create_foreign_key(
        'fk_finance_events_user_id_users',
        'finance_events',
        'users',
        ['user_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.add_column('memories', sa.Column('user_id', sa.BigInteger(), nullable=True))
    connection.execute(sa.update(memories).values(user_id=owner_id))
    op.alter_column('memories', 'user_id', nullable=False)
    op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)
    op.create_foreign_key(
        'fk_memories_user_id_users',
        'memories',
        'users',
        ['user_id'],
        ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    """Revert the ownership schema without deleting user-owned records."""
    op.drop_constraint('fk_memories_user_id_users', 'memories', type_='foreignkey')
    op.drop_index(op.f('ix_memories_user_id'), table_name='memories')
    op.drop_column('memories', 'user_id')
    op.drop_constraint('fk_finance_events_user_id_users', 'finance_events', type_='foreignkey')
    op.drop_index(op.f('ix_finance_events_user_id'), table_name='finance_events')
    op.drop_column('finance_events', 'user_id')
    op.drop_index(op.f('ix_user_identities_user_id'), table_name='user_identities')
    op.drop_table('user_identities')
    op.drop_table('users')
