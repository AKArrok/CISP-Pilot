"""thread-scoped attribution correction events

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "question_attribution_corrections" in existing:
        return
    op.create_table(
        "question_attribution_corrections",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True, autoincrement=True,
        ),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("corrected_point_id", sa.String(length=64), nullable=False),
        sa.Column("corrected_domain", sa.String(length=32), nullable=False),
        sa.Column("previous_point_id", sa.String(length=64), nullable=True),
        sa.Column("previous_status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    for column in (
        "thread_id", "question_id", "corrected_point_id", "corrected_domain",
    ):
        op.create_index(
            f"ix_question_attribution_corrections_{column}",
            "question_attribution_corrections", [column],
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "question_attribution_corrections" in existing:
        op.drop_table("question_attribution_corrections")
