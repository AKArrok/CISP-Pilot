"""conservative question attribution and thread calibration queue

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if column.name not in {item["name"] for item in sa.inspect(bind).get_columns(table)}:
        op.add_column(table, column)


def _add_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    bind = op.get_bind()
    if name not in {item["name"] for item in sa.inspect(bind).get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    # These columns make raw attempts auditable without changing the old API.
    # Existing rows are treated as legacy confirmed evidence for compatibility;
    # new uncertain attempts explicitly set profile_applied=false.
    if "learner_attempts" in existing:
        _add_column_if_missing(
            "learner_attempts",
            sa.Column("attribution_status", sa.String(length=16), nullable=False,
                      server_default="confirmed"),
        )
        _add_column_if_missing(
            "learner_attempts",
            sa.Column("profile_applied", sa.Boolean(), nullable=False,
                      server_default=sa.text("1")),
        )
        _add_column_if_missing(
            "learner_attempts",
            sa.Column("calibration_id", sa.BigInteger(), nullable=True),
        )
        _add_index_if_missing(
            "ix_learner_attempts_attribution_status",
            "learner_attempts", ["attribution_status"],
        )
        _add_index_if_missing(
            "ix_learner_attempts_profile_applied",
            "learner_attempts", ["profile_applied"],
        )
        _add_index_if_missing(
            "ix_learner_attempts_calibration_id",
            "learner_attempts", ["calibration_id"],
        )

    if "question_attributions" not in existing:
        op.create_table(
            "question_attributions",
            sa.Column("question_id", sa.String(length=64), primary_key=True),
            sa.Column("candidate_point_id", sa.String(length=64), nullable=True),
            sa.Column("candidate_domain", sa.String(length=32), nullable=True),
            sa.Column("attribution_status", sa.String(length=16), nullable=False),
            sa.Column("agreement_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("classifier_version", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_question_attributions_candidate_point_id",
                        "question_attributions", ["candidate_point_id"])
        op.create_index("ix_question_attributions_candidate_domain",
                        "question_attributions", ["candidate_domain"])
        op.create_index("ix_question_attributions_attribution_status",
                        "question_attributions", ["attribution_status"])

    if "question_attribution_events" not in existing:
        op.create_table(
            "question_attribution_events",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("question_id", sa.String(length=64), nullable=False),
            sa.Column("attribution_status", sa.String(length=16), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("classifier_version", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("created_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_question_attribution_events_question_id",
                        "question_attribution_events", ["question_id"])
        op.create_index("ix_question_attribution_events_attribution_status",
                        "question_attribution_events", ["attribution_status"])

    if "calibration_hypotheses" not in existing:
        op.create_table(
            "calibration_hypotheses",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("knowledge_point_id", sa.String(length=64), nullable=False),
            sa.Column("trigger_question_id", sa.String(length=64), nullable=False),
            sa.Column("trigger_correct", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False,
                      server_default="pending"),
            sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("calibration_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("presentation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_presented_at", sa.Float(), nullable=True),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
        )
        op.create_index("ix_calibration_hypotheses_thread_id",
                        "calibration_hypotheses", ["thread_id"])
        op.create_index("ix_calibration_hypotheses_knowledge_point_id",
                        "calibration_hypotheses", ["knowledge_point_id"])
        op.create_index("ix_calibration_hypotheses_status",
                        "calibration_hypotheses", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in ("calibration_hypotheses", "question_attribution_events",
                  "question_attributions"):
        if table in existing:
            op.drop_table(table)
    if "learner_attempts" in existing:
        columns = {item["name"] for item in sa.inspect(bind).get_columns("learner_attempts")}
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes("learner_attempts")}
        for name in ("ix_learner_attempts_calibration_id",
                     "ix_learner_attempts_profile_applied",
                     "ix_learner_attempts_attribution_status"):
            if name in indexes:
                op.drop_index(name, table_name="learner_attempts")
        for name in ("calibration_id", "profile_applied", "attribution_status"):
            if name in columns:
                op.drop_column("learner_attempts", name)
