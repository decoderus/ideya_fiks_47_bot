"""broadcast audience

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-19 21:10:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0024'
down_revision: str | None = '0023'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ('broadcasts', 'broadcast_drafts'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('audience', sa.String(length=32), nullable=True))


def downgrade() -> None:
    for table in ('broadcasts', 'broadcast_drafts'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('audience')
