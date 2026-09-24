"""diagnostic sessions, auditable taxonomy, and learner knowledge state

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11

"""
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy metadata keeps the SQLite/MySQL schema definition in one place;
    # create_all is additive here and leaves existing tables untouched.
    from storage.models import Base
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # Drop only objects introduced by this revision; historical user data stays.
    for table in (
        "learner_knowledge_states",
        "question_knowledge_points",
        "knowledge_points",
        "diagnostic_events",
        "diagnostic_sessions",
    ):
        op.drop_table(table, if_exists=True)
