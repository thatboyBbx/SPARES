"""phase4 sales customers

Revision ID: e22047c8ee5a
Revises: 962efee40ab1
Create Date: 2026-08-03 20:32:09.358223

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e22047c8ee5a'
down_revision: Union[str, None] = '962efee40ab1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYNC_STATUS_ENUM = sa.Enum('PENDING', 'SYNCED', 'CONFLICT', 'FAILED', name='sync_status')


def _pick_owner_id(bind) -> str | None:
    owner_id = bind.execute(sa.text("SELECT id FROM users WHERE role = 'owner' LIMIT 1")).scalar()
    if owner_id is None:
        owner_id = bind.execute(sa.text("SELECT id FROM users LIMIT 1")).scalar()
    return owner_id


def upgrade() -> None:
    bind = op.get_bind()
    owner_id = _pick_owner_id(bind)

    op.create_table('customers',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False, comment='Client-generatable UUID (string form, portable across Postgres/SQLite) — never an auto-increment integer.'),
    sa.Column('device_id', sa.String(length=64), nullable=False, comment='Identifier of the device that created/last touched this row.'),
    sa.Column('created_by_id', sa.String(length=36), nullable=False, comment='Which user performed the action.'),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, comment='Incremented on every update; used for sync conflict detection.'),
    sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=False, comment='SYNCED when written directly server-side; PENDING when queued from a device.'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    # --- sales: mixin adoption; own created_at column already had this exact
    # name, so it needs no backfill/rename, only the remaining mixin columns
    # (plus the new nullable customer_id) are new. ---
    with op.batch_alter_table("sales") as batch_op:
        batch_op.add_column(sa.Column('customer_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE sales SET device_id = 'system-import', created_by_id = :owner_id, "
            "updated_at = created_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("sales") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_index('ix_sales_customer_id', ['customer_id'])
        batch_op.create_foreign_key('fk_sales_customer_id_customers', 'customers', ['customer_id'], ['id'])
        batch_op.create_foreign_key('fk_sales_created_by_id_users', 'users', ['created_by_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table("sales") as batch_op:
        batch_op.drop_constraint('fk_sales_created_by_id_users', type_='foreignkey')
        batch_op.drop_constraint('fk_sales_customer_id_customers', type_='foreignkey')
        batch_op.drop_index('ix_sales_customer_id')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('customer_id')

    op.drop_table('customers')
