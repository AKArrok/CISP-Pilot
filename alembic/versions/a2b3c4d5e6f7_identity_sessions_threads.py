"""add account identities, web sessions, and owned threads

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _check_existing_table(inspector, name, columns, *, unique=(), foreign_keys=(), indexes=()) -> None:
    actual = {column["name"]: column for column in inspector.get_columns(name)}
    for column_name, (kind, length, nullable) in columns.items():
        column = actual.get(column_name)
        if (
            column is None
            or not isinstance(column["type"], kind)
            or (length is not None and column["type"].length != length)
            or bool(column["nullable"]) != nullable
        ):
            raise RuntimeError(f"Existing {name}.{column_name} does not match identity schema")
    if inspector.get_pk_constraint(name)["constrained_columns"] != ["id"]:
        raise RuntimeError(f"Existing {name} primary key does not match identity schema")
    actual_indexes = {item["name"]: item for item in inspector.get_indexes(name)}
    actual_unique = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)
    } | {
        tuple(item["column_names"]) for item in actual_indexes.values() if item.get("unique")
    }
    if not set(unique) <= actual_unique:
        raise RuntimeError(f"Existing {name} unique constraints do not match identity schema")
    actual_fks = {
        (tuple(item["constrained_columns"]), item["referred_table"],
         tuple(item["referred_columns"]))
        for item in inspector.get_foreign_keys(name)
    }
    if not set(foreign_keys) <= actual_fks:
        raise RuntimeError(f"Existing {name} foreign keys do not match identity schema")
    for index_name, index_columns in indexes:
        item = actual_indexes.get(index_name)
        if item is None or item["column_names"] != list(index_columns):
            raise RuntimeError(f"Existing {name} indexes do not match identity schema")


def upgrade() -> None:
    # The initial revision creates current ORM metadata on fresh databases.
    # Existing databases at f1a2b3c4d5e6 still need these tables added.
    # Offline SQL has no inspectable connection, so emit DDL directly.
    offline = op.get_context().as_sql
    tables = set()
    if not offline:
        inspector = sa.inspect(op.get_bind())
        tables = set(inspector.get_table_names())
        if "users" in tables:
            _check_existing_table(inspector, "users", {
                "id": (sa.String, 36, False),
                "username": (sa.String, 255, False),
                "password_hash": (sa.String, 512, False),
                "created_at": (sa.DateTime, None, False),
            }, unique=(("username",),))
        if "web_sessions" in tables:
            _check_existing_table(inspector, "web_sessions", {
                "id": (sa.String, 36, False),
                "token_hash": (sa.String, 64, False),
                "user_id": (sa.String, 36, False),
                "expires_at": (sa.DateTime, None, False),
                "revoked_at": (sa.DateTime, None, True),
            }, unique=(("token_hash",),),
                foreign_keys=((("user_id",), "users", ("id",)),),
                indexes=(("ix_web_sessions_user_id", ("user_id",)),))
        if "threads" in tables:
            _check_existing_table(inspector, "threads", {
                "id": (sa.String, 36, False),
                "owner_user_id": (sa.String, 36, False),
                "created_at": (sa.DateTime, None, False),
            }, foreign_keys=((("owner_user_id",), "users", ("id",)),),
                indexes=(("ix_threads_owner_user_id", ("owner_user_id",)),))
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("username", sa.String(length=255), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(length=512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "web_sessions" not in tables:
        op.create_table(
            "web_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])
    if "threads" not in tables:
        op.create_table(
            "threads",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("owner_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_threads_owner_user_id", "threads", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_threads_owner_user_id", table_name="threads")
    op.drop_table("threads")
    op.drop_index("ix_web_sessions_user_id", table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_table("users")
