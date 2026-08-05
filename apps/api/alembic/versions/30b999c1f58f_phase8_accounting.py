"""phase8 accounting

Revision ID: 30b999c1f58f
Revises: b6ac351f3eb1
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30b999c1f58f'
down_revision: Union[str, None] = 'b6ac351f3eb1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SYNC_STATUS_ENUM = sa.Enum('PENDING', 'SYNCED', 'CONFLICT', 'FAILED', name='sync_status')


def upgrade() -> None:
    op.create_table('supplier_payments',
    sa.Column('reference', sa.String(length=80), nullable=False),
    sa.Column('supplier_id', sa.String(length=36), nullable=False),
    sa.Column('purchase_order_id', sa.String(length=36), nullable=True),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('method', sa.String(length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=80), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False, comment='Client-generatable UUID (string form, portable across Postgres/SQLite) — never an auto-increment integer.'),
    sa.Column('device_id', sa.String(length=64), nullable=False, comment='Identifier of the device that created/last touched this row.'),
    sa.Column('created_by_id', sa.String(length=36), nullable=False, comment='Which user performed the action.'),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, comment='Incremented on every update; used for sync conflict detection.'),
    sa.Column('sync_status', SYNC_STATUS_ENUM, nullable=False, comment='SYNCED when written directly server-side; PENDING when queued from a device.'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key'),
    sa.UniqueConstraint('reference')
    )
    op.create_index(op.f('ix_supplier_payments_purchase_order_id'), 'supplier_payments', ['purchase_order_id'], unique=False)
    op.create_index(op.f('ix_supplier_payments_supplier_id'), 'supplier_payments', ['supplier_id'], unique=False)

    op.create_table('journal_entries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('entry_type', sa.String(length=30), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('branch_id', sa.String(length=36), nullable=True),
    sa.Column('reference', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('related_entity_type', sa.String(length=40), nullable=True),
    sa.Column('related_entity_id', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_journal_entries_branch_id'), 'journal_entries', ['branch_id'], unique=False)
    op.create_index(op.f('ix_journal_entries_created_at'), 'journal_entries', ['created_at'], unique=False)
    op.create_index(op.f('ix_journal_entries_entry_type'), 'journal_entries', ['entry_type'], unique=False)
    op.create_index(op.f('ix_journal_entries_reference'), 'journal_entries', ['reference'], unique=False)
    op.create_index(op.f('ix_journal_entries_related_entity_id'), 'journal_entries', ['related_entity_id'], unique=False)
    op.create_index(op.f('ix_journal_entries_related_entity_type'), 'journal_entries', ['related_entity_type'], unique=False)
    op.create_index('ix_journal_entries_type_created', 'journal_entries', ['entry_type', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_journal_entries_type_created', table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_related_entity_type'), table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_related_entity_id'), table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_reference'), table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_entry_type'), table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_created_at'), table_name='journal_entries')
    op.drop_index(op.f('ix_journal_entries_branch_id'), table_name='journal_entries')
    op.drop_table('journal_entries')

    op.drop_index(op.f('ix_supplier_payments_supplier_id'), table_name='supplier_payments')
    op.drop_index(op.f('ix_supplier_payments_purchase_order_id'), table_name='supplier_payments')
    op.drop_table('supplier_payments')
