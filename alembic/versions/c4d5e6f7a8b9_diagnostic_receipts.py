"""Atomic diagnostic start/answer receipts and frozen completed reports.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def _receipt_table(name, first_key, second_key, second_length):
    columns = (
        sa.Column(first_key, sa.String(64 if first_key == "thread_id" else 36), primary_key=True),
        sa.Column(second_key, sa.String(second_length).with_variant(
            sa.String(second_length, collation="utf8mb4_bin"), "mysql"), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    if not op.get_context().as_sql:
        inspector = sa.inspect(op.get_bind())
        if name in inspector.get_table_names():
            actual = {item["name"]: item for item in inspector.get_columns(name)}
            for expected in columns:
                item = actual.get(expected.name)
                if (item is None or item["nullable"]
                        or not isinstance(item["type"], type(expected.type))
                        or (isinstance(expected.type, sa.String)
                            and expected.type.length is not None
                            and item["type"].length != expected.type.length)):
                    raise RuntimeError(f"Existing {name}.{expected.name} does not match diagnostic schema")
            if inspector.get_pk_constraint(name)["constrained_columns"] != [first_key, second_key]:
                raise RuntimeError(f"Existing {name} primary key does not match diagnostic schema")
            if op.get_bind().dialect.name == "mysql":
                collation = op.get_bind().execute(sa.text(
                    "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
                    "AND COLUMN_NAME = :column"
                ), {"table": name, "column": second_key}).scalar()
                if collation != "utf8mb4_bin":
                    raise RuntimeError(f"Existing {name} receipt key must use utf8mb4_bin")
            return
    op.create_table(name, *columns)


def upgrade() -> None:
    if not op.get_context().as_sql:
        inspector = sa.inspect(op.get_bind())
        columns = {item["name"]: item for item in inspector.get_columns("diagnostic_sessions")}
        if "report_json" in columns:
            column = columns["report_json"]
            if not isinstance(column["type"], sa.Text) or not column["nullable"]:
                raise RuntimeError("Existing diagnostic_sessions.report_json does not match diagnostic schema")
        else:
            op.add_column("diagnostic_sessions", sa.Column("report_json", sa.Text(), nullable=True))
    else:
        op.add_column("diagnostic_sessions", sa.Column("report_json", sa.Text(), nullable=True))
    _receipt_table("diagnostic_start_receipts", "thread_id", "start_id", 128)
    _receipt_table("diagnostic_answer_receipts", "session_id", "question_id", 64)


def downgrade() -> None:
    op.drop_table("diagnostic_answer_receipts")
    op.drop_table("diagnostic_start_receipts")
    op.drop_column("diagnostic_sessions", "report_json")
