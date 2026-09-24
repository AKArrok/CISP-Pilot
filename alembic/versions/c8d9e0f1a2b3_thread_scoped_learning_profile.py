"""thread-scoped attempts and review cards

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-09-13
"""
from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "learner_attempts" not in existing:
        op.create_table(
            "learner_attempts",
            sa.Column(
                "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True, autoincrement=True,
            ),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("question_id", sa.String(length=64), nullable=False),
            sa.Column("question_source", sa.String(length=64), nullable=False),
            sa.Column("domain", sa.String(length=32), nullable=True),
            sa.Column("stem", sa.Text(), nullable=True),
            sa.Column("choice", sa.String(length=8), nullable=False),
            sa.Column("correct", sa.Boolean(), nullable=False),
            sa.Column("answered_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_learner_attempts_thread_id", "learner_attempts", ["thread_id"])
        op.create_index("ix_learner_attempts_question_id", "learner_attempts", ["question_id"])
        op.create_index("ix_learner_attempts_domain", "learner_attempts", ["domain"])

    if "learner_review_cards" not in existing:
        op.create_table(
            "learner_review_cards",
            sa.Column("thread_id", sa.String(length=64), primary_key=True),
            sa.Column("question_id", sa.String(length=64), primary_key=True),
            sa.Column("domain", sa.String(length=32), nullable=True),
            sa.Column("state", sa.Integer(), nullable=False),
            sa.Column("step", sa.Integer(), nullable=True),
            sa.Column("stability", sa.Float(), nullable=False),
            sa.Column("difficulty", sa.Float(), nullable=False),
            sa.Column("due", sa.Float(), nullable=False),
            sa.Column("last_review", sa.Float(), nullable=True),
            sa.Column("reps", sa.Integer(), nullable=False),
            sa.Column("lapses", sa.Integer(), nullable=False),
        )
        op.create_index(
            "ix_learner_review_cards_domain", "learner_review_cards", ["domain"]
        )
        op.create_index("ix_learner_review_cards_due", "learner_review_cards", ["due"])


def downgrade() -> None:
    op.drop_table("learner_review_cards")
    op.drop_table("learner_attempts")
