"""Durable per-thread ordinary answer receipts.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
"""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().as_sql:
        inspector = sa.inspect(op.get_bind())
        if "answer_submissions" in inspector.get_table_names():
            columns = {item["name"]: item for item in inspector.get_columns("answer_submissions")}
            expected = {
                "thread_id": (sa.String, 64),
                "submission_id": (sa.String, 128),
                "question_id": (sa.String, 64),
                "choice": (sa.String, 8),
                "result_json": (sa.Text, None),
                "created_at": (sa.Float, None),
            }
            for name, (kind, length) in expected.items():
                column = columns.get(name)
                if (column is None or column["nullable"]
                    or not isinstance(column["type"], kind)
                    or (length is not None and column["type"].length != length)):
                    raise RuntimeError(f"Existing answer_submissions.{name} does not match receipt schema")
            if inspector.get_pk_constraint("answer_submissions")["constrained_columns"] != ["thread_id", "submission_id"]:
                raise RuntimeError("Existing answer_submissions primary key does not match receipt schema")
            if op.get_bind().dialect.name == "mysql":
                collation = op.get_bind().execute(sa.text(
                    "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'answer_submissions' "
                    "AND COLUMN_NAME = 'submission_id'"
                )).scalar()
                if collation != "utf8mb4_bin":
                    raise RuntimeError("Existing answer_submissions submission key must use utf8mb4_bin")
            return
    op.create_table(
        "answer_submissions",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("submission_id", sa.String(128).with_variant(
            sa.String(128, collation="utf8mb4_bin"), "mysql"), primary_key=True),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("choice", sa.String(8), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("answer_submissions")
