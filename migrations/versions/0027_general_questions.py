"""general questions

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-23 14:22:40.780688
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0027'
down_revision: str | None = '0026'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('quest_questions', schema=None) as batch_op:
        batch_op.alter_column('quest_id',
               existing_type=sa.INTEGER(),
               nullable=True)



def downgrade() -> None:
    with op.batch_alter_table('quest_questions', schema=None) as batch_op:
        batch_op.alter_column('quest_id',
               existing_type=sa.INTEGER(),
               nullable=False)

