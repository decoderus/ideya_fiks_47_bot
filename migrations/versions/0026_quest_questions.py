"""quest questions

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-23 13:56:34.441876
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0026'
down_revision: str | None = '0025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('quest_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('quest_id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['quest_id'], ['quests.id'], ),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quest_questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quest_questions_quest_id'), ['quest_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quest_questions_student_id'), ['student_id'], unique=False)

    op.create_table('quest_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('author_role', sa.Enum('admin', 'moderator', 'teacher', 'student', name='role'), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['question_id'], ['quest_questions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quest_answers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quest_answers_question_id'), ['question_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('quest_answers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quest_answers_question_id'))

    op.drop_table('quest_answers')
    with op.batch_alter_table('quest_questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quest_questions_student_id'))
        batch_op.drop_index(batch_op.f('ix_quest_questions_quest_id'))

    op.drop_table('quest_questions')
