"""survey

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-26 07:43:54.879749
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0031'
down_revision: str | None = '0030'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('survey_responses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('survey', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('admin', 'moderator', 'teacher', 'student', name='role'), nullable=False),
    sa.Column('answers', sa.JSON(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('survey', 'user_id')
    )


def downgrade() -> None:
    op.drop_table('survey_responses')
