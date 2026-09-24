"""record the knowledge point actually applied by each learner attempt

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-09-19
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("learner_attempts")
    }
    if "attribution_point_id" not in columns:
        op.add_column(
            "learner_attempts",
            sa.Column("attribution_point_id", sa.String(length=64), nullable=True),
        )
    indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("learner_attempts")
    }
    if "ix_learner_attempts_attribution_point_id" not in indexes:
        op.create_index(
            "ix_learner_attempts_attribution_point_id",
            "learner_attempts", ["attribution_point_id"],
        )

    correction_columns = {
        item["name"]
        for item in sa.inspect(bind).get_columns("question_attribution_corrections")
    }
    additions = (
        sa.Column("replayed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "retracted_calibrations", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("replacement_calibration_id", sa.BigInteger(), nullable=True),
    )
    for column in additions:
        if column.name not in correction_columns:
            op.add_column("question_attribution_corrections", column)


def downgrade() -> None:
    bind = op.get_bind()
    correction_columns = {
        item["name"]
        for item in sa.inspect(bind).get_columns("question_attribution_corrections")
    }
    for name in (
        "replacement_calibration_id",
        "retracted_calibrations",
        "replayed_attempts",
    ):
        if name in correction_columns:
            op.drop_column("question_attribution_corrections", name)

    indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("learner_attempts")
    }
    if "ix_learner_attempts_attribution_point_id" in indexes:
        op.drop_index(
            "ix_learner_attempts_attribution_point_id",
            table_name="learner_attempts",
        )
    columns = {
        item["name"] for item in sa.inspect(bind).get_columns("learner_attempts")
    }
    if "attribution_point_id" in columns:
        op.drop_column("learner_attempts", "attribution_point_id")
