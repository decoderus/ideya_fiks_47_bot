"""wheel loss

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-23 14:35:02.676943
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0028'
down_revision: str | None = '0027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('rewards', schema=None) as batch_op:
        batch_op.add_column(sa.Column('loss_chance', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('loss', sa.Integer(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('rewards', schema=None) as batch_op:
        batch_op.drop_column('loss')
        batch_op.drop_column('loss_chance')

