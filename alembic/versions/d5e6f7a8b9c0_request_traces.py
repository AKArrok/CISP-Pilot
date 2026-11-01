"""Add request observations without adopting or backfilling legacy requests.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("request_id", sa.String(36).with_variant(
            sa.String(36, collation="utf8mb4_bin"), "mysql"), primary_key=True),
        sa.Column("thread_id", sa.String(36), sa.ForeignKey("threads.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trace_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    if not op.get_context().as_sql:
        inspector = sa.inspect(op.get_bind())
        if "request_traces" in inspector.get_table_names():
            actual = {item["name"]: item for item in inspector.get_columns("request_traces")}
            for expected in columns:
                item = actual.get(expected.name)
                if (item is None or item["nullable"]
                        or not isinstance(item["type"], type(expected.type))
                        or (isinstance(expected.type, sa.String)
                            and expected.type.length is not None
                            and item["type"].length != expected.type.length)):
                    raise RuntimeError(f"Existing request_traces.{expected.name} does not match trace schema")
            if inspector.get_pk_constraint("request_traces")["constrained_columns"] != ["request_id"]:
                raise RuntimeError("Existing request_traces primary key does not match trace schema")
            foreign_keys = inspector.get_foreign_keys("request_traces")
            if not any(item["constrained_columns"] == ["thread_id"]
                       and item["referred_table"] == "threads"
                       and item["referred_columns"] == ["id"] for item in foreign_keys):
                raise RuntimeError("Existing request_traces foreign key does not match trace schema")
            indexes = {item["name"]: item for item in inspector.get_indexes("request_traces")}
            if indexes.get("ix_request_traces_thread_id", {}).get("column_names") != ["thread_id"]:
                raise RuntimeError("Existing request_traces index does not match trace schema")
            if op.get_bind().dialect.name == "mysql":
                collation = op.get_bind().execute(sa.text(
                    "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'request_traces' "
                    "AND COLUMN_NAME = 'request_id'"
                )).scalar()
                if collation != "utf8mb4_bin":
                    raise RuntimeError("Existing request_traces.request_id must use utf8mb4_bin")
            return
    op.create_table("request_traces", *columns)
    op.create_index("ix_request_traces_thread_id", "request_traces", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_request_traces_thread_id", table_name="request_traces")
    op.drop_table("request_traces")
