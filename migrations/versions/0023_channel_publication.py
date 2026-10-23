"""channel publication

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-19 20:20:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0023'
down_revision: str | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('quests', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('channel_post', sa.Boolean(), server_default='0', nullable=False)
        )
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('publication', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('publication_note', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('publication_draft', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.drop_column('publication_draft')
        batch_op.drop_column('publication_note')
        batch_op.drop_column('publication')
    with op.batch_alter_table('quests', schema=None) as batch_op:
        batch_op.drop_column('channel_post')
