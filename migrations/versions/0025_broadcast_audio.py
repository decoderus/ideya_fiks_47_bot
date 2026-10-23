"""broadcast audio

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-19 22:20:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0025'
down_revision: str | None = '0024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ('broadcasts', 'broadcast_drafts'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('audio', sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ('broadcasts', 'broadcast_drafts'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('audio')
