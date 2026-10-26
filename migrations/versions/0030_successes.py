"""successes

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-26 06:47:32.245334
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0030'
down_revision: str | None = '0029'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('successes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('author_role', sa.Enum('admin', 'moderator', 'teacher', 'student', name='role'), nullable=False),
    sa.Column('category', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('coins', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('successes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_successes_student_id'), ['student_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('successes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_successes_student_id'))

    op.drop_table('successes')
