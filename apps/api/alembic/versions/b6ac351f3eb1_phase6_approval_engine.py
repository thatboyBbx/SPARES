"""phase6 approval engine

Revision ID: b6ac351f3eb1
Revises: 447234ff298e
Create Date: 2026-08-03 20:53:33.905190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6ac351f3eb1'
down_revision: Union[str, None] = '447234ff298e'
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

    # --- approvals: mixin adoption; own created_at column already had this
    # exact name, so only the remaining mixin columns are new, alongside the
    # genuinely-new deadline/related_entity/approver_id business columns. ---
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column('deadline', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('related_entity_type', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('related_entity_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('approver_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE approvals SET device_id = 'system-import', created_by_id = :owner_id, "
            "updated_at = created_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_index('ix_approvals_related_entity_id', ['related_entity_id'])
        batch_op.create_index('ix_approvals_related_entity_type', ['related_entity_type'])
        batch_op.create_foreign_key('fk_approvals_created_by_id_users', 'users', ['created_by_id'], ['id'])
        batch_op.create_foreign_key('fk_approvals_approver_id_users', 'users', ['approver_id'], ['id'])

    # --- expenses: mixin adoption; same pattern, no genuinely new business
    # columns this phase. ---
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.add_column(sa.Column('device_id', sa.String(length=64), nullable=True, comment='Identifier of the device that created/last touched this row.'))
        batch_op.add_column(sa.Column('created_by_id', sa.String(length=36), nullable=True, comment='Which user performed the action.'))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('version', sa.Integer(), nullable=True, comment='Incremented on every update; used for sync conflict detection.'))
        batch_op.add_column(sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=True, comment='SYNCED when written directly server-side; PENDING when queued from a device.'))

    op.execute(
        sa.text(
            "UPDATE expenses SET device_id = 'system-import', created_by_id = :owner_id, "
            "updated_at = created_at, version = 1, sync_status = 'SYNCED'"
        ).bindparams(owner_id=owner_id)
    )

    with op.batch_alter_table("expenses") as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('created_by_id', existing_type=sa.String(length=36), nullable=False)
        batch_op.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.alter_column('version', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('sync_status', existing_type=SYNC_STATUS_ENUM, nullable=False)
        batch_op.create_foreign_key('fk_expenses_created_by_id_users', 'users', ['created_by_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table("expenses") as batch_op:
        batch_op.drop_constraint('fk_expenses_created_by_id_users', type_='foreignkey')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')

    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_constraint('fk_approvals_approver_id_users', type_='foreignkey')
        batch_op.drop_constraint('fk_approvals_created_by_id_users', type_='foreignkey')
        batch_op.drop_index('ix_approvals_related_entity_type')
        batch_op.drop_index('ix_approvals_related_entity_id')
        batch_op.drop_column('sync_status')
        batch_op.drop_column('version')
        batch_op.drop_column('updated_at')
        batch_op.drop_column('created_by_id')
        batch_op.drop_column('device_id')
        batch_op.drop_column('approver_id')
        batch_op.drop_column('related_entity_id')
        batch_op.drop_column('related_entity_type')
        batch_op.drop_column('deadline')
