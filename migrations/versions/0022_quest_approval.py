"""quest approval

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-19 18:10:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('quests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('approval', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('approval_note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('quests', schema=None) as batch_op:
        batch_op.drop_column('approval_note')
        batch_op.drop_column('approval')
