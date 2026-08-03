"""phase2 inventory engine

Revision ID: dc2560377714
Revises: e78fd2eb4b8f
Create Date: 2026-08-03 19:46:37.812941

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dc2560377714'
down_revision: Union[str, None] = 'e78fd2eb4b8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYNC_STATUS_ENUM = sa.Enum('PENDING', 'SYNCED', 'CONFLICT', 'FAILED', name='sync_status')


def _pick_owner_id(bind) -> str | None:
    """Any existing row backfilled by this migration needs a created_by_id;
    prefer an owner account, but fall back to any user so this doesn't hard
    fail on unusual seed data."""
    owner_id = bind.execute(sa.text("SELECT id FROM users WHERE role = 'owner' LIMIT 1")).scalar()
    if owner_id is None:
        owner_id = bind.execute(sa.text("SELECT id FROM users LIMIT 1")).scalar()
    return owner_id


def upgrade() -> None:
    bind = op.get_bind()
    owner_id = _pick_owner_id(bind)

    op.create_table('categories',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False, comment='Client-generatable UUID (string form, portable across Postgres/SQLite) — never an auto-increment integer.'),
    sa.Column('device_id', sa.String(length=64), nullable=False, comment='Identifier of the device that created/last touched this row.'),
    sa.Column('created_by_id', sa.String(length=36), nullable=False, comment='Which user performed the action.'),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, comment='Incremented on every update; used for sync conflict detection.'),
    sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=False, comment='SYNCED when written directly server-side; PENDING when queued from a device.'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['parent_id'], ['categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    # --- products: add nullable-first, backfill existing rows, then enforce NOT NULL ---
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column('category_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE products SET device_id = 'system-import', created_by_id = :owner_id, "
            "created_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_index('ix_products_category_id', ['category_id'])
        batch_op.create_foreign_key('fk_products_created_by_id_users', 'users', ['created_by_id'], ['id'])
        batch_op.create_foreign_key('fk_products_category_id_categories', 'categories', ['category_id'], ['id'])

    op.create_index('ix_stock_movements_product_branch', 'stock_movements', ['product_id', 'branch_id'], unique=False)

    # --- transfers: same nullable-first pattern; created_at is backfilled from
    # the outgoing requested_at column before that column is dropped. ---
    with op.batch_alter_table("transfers") as batch_op:
        batch_op.add_column(sa.Column('fulfilled_by_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('received_by_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE transfers SET created_at = requested_at, updated_at = requested_at, "
            "device_id = 'system-import', created_by_id = :owner_id, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("transfers") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_foreign_key('fk_transfers_received_by_id_users', 'users', ['received_by_id'], ['id'])
        batch_op.create_foreign_key('fk_transfers_fulfilled_by_id_users', 'users', ['fulfilled_by_id'], ['id'])
        batch_op.create_foreign_key('fk_transfers_created_by_id_users', 'users', ['created_by_id'], ['id'])
        batch_op.drop_column('requested_at')


def downgrade() -> None:
    with op.batch_alter_table("transfers") as batch_op:
        batch_op.add_column(sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True))

    op.execute(sa.text("UPDATE transfers SET requested_at = created_at"))

    with op.batch_alter_table("transfers") as batch_op:
        batch_op.alter_column('requested_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.drop_constraint('fk_transfers_created_by_id_users', type_='foreignkey')
        batch_op.drop_constraint('fk_transfers_fulfilled_by_id_users', type_='foreignkey')
        batch_op.drop_constraint('fk_transfers_received_by_id_users', type_='foreignkey')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('fulfilled_at')
        batch_op.drop_column('received_by_id')
        batch_op.drop_column('fulfilled_by_id')

    op.drop_index('ix_stock_movements_product_branch', table_name='stock_movements')

    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint('fk_products_category_id_categories', type_='foreignkey')
        batch_op.drop_constraint('fk_products_created_by_id_users', type_='foreignkey')
        batch_op.drop_index('ix_products_category_id')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('category_id')

    op.drop_table('categories')
