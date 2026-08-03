"""phase3 sync sweep and idempotency

Revision ID: 962efee40ab1
Revises: dc2560377714
Create Date: 2026-08-03 20:17:31.593393

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '962efee40ab1'
down_revision: Union[str, None] = 'dc2560377714'
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

    # --- nullable-only additions: no backfill needed ---
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column('idempotency_key', sa.String(length=80), nullable=True))
        batch_op.create_unique_constraint('uq_approvals_idempotency_key', ['idempotency_key'])

    with op.batch_alter_table("expenses") as batch_op:
        batch_op.add_column(sa.Column('idempotency_key', sa.String(length=80), nullable=True))
        batch_op.create_unique_constraint('uq_expenses_idempotency_key', ['idempotency_key'])

    # --- stock_movements: mixin adoption; created_at already exists (kept,
    # not touched) so only the remaining mixin columns are new. ---
    with op.batch_alter_table("stock_movements") as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE stock_movements SET device_id = 'system-import', created_by_id = :owner_id, "
            "updated_at = created_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("stock_movements") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_foreign_key('fk_stock_movements_created_by_id_users', 'users', ['created_by_id'], ['id'])

    # --- purchase_receipts: mixin adoption; received_at is kept as its own
    # legacy display field, created_at is new (backfilled from received_at). ---
    with op.batch_alter_table("purchase_receipts") as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE purchase_receipts SET device_id = 'system-import', created_by_id = :owner_id, "
            "created_at = received_at, updated_at = received_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("purchase_receipts") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_foreign_key('fk_purchase_receipts_created_by_id_users', 'users', ['created_by_id'], ['id'])

    # --- purchase_orders: mixin adoption; requested_at/created_by (legacy
    # string) are kept, created_at is new (backfilled from requested_at). ---
    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.add_column(sa.Column('idempotency_key', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE purchase_orders SET device_id = 'system-import', created_by_id = :owner_id, "
            "created_at = requested_at, updated_at = requested_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_unique_constraint('uq_purchase_orders_idempotency_key', ['idempotency_key'])
        batch_op.create_foreign_key('fk_purchase_orders_created_by_id_users', 'users', ['created_by_id'], ['id'])

    # --- purchase_order_lines: mixin adoption; own id/created_at columns are
    # replaced outright by the mixin's (backfilled from the existing values). ---
    with op.batch_alter_table("purchase_order_lines") as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE purchase_order_lines SET device_id = 'system-import', created_by_id = :owner_id, "
            "updated_at = created_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("purchase_order_lines") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_foreign_key('fk_purchase_order_lines_created_by_id_users', 'users', ['created_by_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table("purchase_order_lines") as batch_op:
        batch_op.drop_constraint('fk_purchase_order_lines_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')

    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.drop_constraint('fk_purchase_orders_created_by_id_users', type_='foreignkey')
        batch_op.drop_constraint('uq_purchase_orders_idempotency_key', type_='unique')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('idempotency_key')

    with op.batch_alter_table("purchase_receipts") as batch_op:
        batch_op.drop_constraint('fk_purchase_receipts_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')

    with op.batch_alter_table("stock_movements") as batch_op:
        batch_op.drop_constraint('fk_stock_movements_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')

    with op.batch_alter_table("expenses") as batch_op:
        batch_op.drop_constraint('uq_expenses_idempotency_key', type_='unique')
        batch_op.drop_column('idempotency_key')

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_constraint('uq_approvals_idempotency_key', type_='unique')
        batch_op.drop_column('idempotency_key')
