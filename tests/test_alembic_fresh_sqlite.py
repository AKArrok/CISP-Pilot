import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from storage.db import SCHEMA_HEAD

ROOT = Path(__file__).resolve().parents[1]


def _migrate(
    db_path: Path,
    command: str,
    revision: str,
    *,
    fallback_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["CISP_DB_PATH"] = str(fallback_path or db_path)
    return subprocess.run(
        [sys.executable, "-B", "-m", "alembic", command, revision],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_head_builds_fresh_sqlite_schema(tmp_path):
    db_path = tmp_path / "fresh.sqlite"
    result = _migrate(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db_path) as connection:
        revision = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                'select name from sqlite_master where type = "table"'
            )
            if not row[0].startswith("sqlite_")
        }
        attempt_columns = {
            row[1]
            for row in connection.execute("pragma table_info(learner_attempts)")
        }
        correction_columns = {
            row[1]
            for row in connection.execute(
                "pragma table_info(question_attribution_corrections)"
            )
        }

    from storage.models import Base

    assert revision == SCHEMA_HEAD
    assert tables - {"alembic_version"} == set(Base.metadata.tables)
    assert "question_attribution_corrections" in tables
    assert "attribution_point_id" in attempt_columns
    assert {
        "replayed_attempts",
        "retracted_calibrations",
        "replacement_calibration_id",
    } <= correction_columns


def test_alembic_upgrade_preserves_existing_attribution_rows(tmp_path):
    db_path = tmp_path / "upgrade.sqlite"
    result = _migrate(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    result = _migrate(db_path, "downgrade", "e0f1a2b3c4d5")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            insert into learner_attempts (
                thread_id, question_id, question_source, domain, stem, choice,
                correct, answered_at, attribution_status, profile_applied,
                calibration_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-before-upgrade", "question-before-upgrade", "fixture",
                "安全工程", "升级前题目", "A", 0, 1234.5, "confirmed", 1, None,
            ),
        )
        connection.execute(
            """
            insert into question_attribution_corrections (
                thread_id, question_id, corrected_point_id, corrected_domain,
                previous_point_id, previous_status, reason, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "thread-before-upgrade", "question-before-upgrade", "kp-new",
                "安全工程", "kp-old", "confirmed", "升级前纠错", 1235.0,
            ),
        )
        connection.commit()

    result = _migrate(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db_path) as connection:
        attempt = connection.execute(
            """
            select thread_id, question_id, domain, profile_applied,
                   attribution_point_id
            from learner_attempts
            """
        ).fetchone()
        correction = connection.execute(
            """
            select thread_id, question_id, corrected_point_id, reason,
                   replayed_attempts, retracted_calibrations,
                   replacement_calibration_id
            from question_attribution_corrections
            """
        ).fetchone()
        revision = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()[0]

    assert revision == SCHEMA_HEAD
    assert attempt == (
        "thread-before-upgrade", "question-before-upgrade", "安全工程", 1, None,
    )
    assert correction == (
        "thread-before-upgrade", "question-before-upgrade", "kp-new",
        "升级前纠错", 0, 0, None,
    )

def test_database_url_sqlite_is_the_authoritative_migration_target(tmp_path):
    target_path = tmp_path / "database-url.sqlite"
    decoy_path = tmp_path / "cisp-db-path.sqlite"

    result = _migrate(
        target_path,
        "upgrade",
        "head",
        fallback_path=decoy_path,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_path.exists()
    assert not decoy_path.exists()
    with sqlite3.connect(target_path) as connection:
        revision = connection.execute(
            "select version_num from alembic_version"
        ).fetchone()[0]
    assert revision == SCHEMA_HEAD
