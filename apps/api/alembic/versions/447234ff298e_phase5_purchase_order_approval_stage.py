"""phase5 purchase order approval stage

Revision ID: 447234ff298e
Revises: e22047c8ee5a
Create Date: 2026-08-03 20:41:15.216593

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '447234ff298e'
down_revision: Union[str, None] = 'e22047c8ee5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.add_column(sa.Column('approved_by_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key('fk_purchase_orders_approved_by_id_users', 'users', ['approved_by_id'], ['id'])

    # The model's default status changed from 'pending' to 'requested' to
    # match the new requested -> approved -> received naming; existing rows
    # created before this migration keep the old literal unless renamed here.
    op.execute("UPDATE purchase_orders SET status = 'requested' WHERE status = 'pending'")


def downgrade() -> None:
    op.execute("UPDATE purchase_orders SET status = 'pending' WHERE status = 'requested'")

    with op.batch_alter_table("purchase_orders") as batch_op:
        batch_op.drop_constraint('fk_purchase_orders_approved_by_id_users', type_='foreignkey')
        batch_op.drop_column('approved_at')
        batch_op.drop_column('approved_by_id')
