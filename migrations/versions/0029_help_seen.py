"""help seen

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-23 17:06:08.513600
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0029'
down_revision: str | None = '0028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('help_seen', sa.Boolean(), server_default='0', nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('help_seen')

