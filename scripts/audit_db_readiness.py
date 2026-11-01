"""Read-only SQLite audit for migration and historical attribution gaps."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from data_ingest.manifest import validate_index  # noqa: E402
from storage.db import SCHEMA_HEAD  # noqa: E402


def audit(path: Path) -> dict:
    path = path.resolve(strict=True)
    if Path(f"{path}-wal").exists():
        raise RuntimeError("数据库存在 WAL 侧文件；请先取得一致性快照再审计")

    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        revision = None
        if "alembic_version" in tables:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None

        count_tables = (
            "questions", "knowledge_points", "question_knowledge_points",
            "question_attributions", "learner_attempts",
            "diagnostic_sessions", "conversations",
        )
        counts = {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in count_tables if table in tables
        }

        attempts = None
        replay = {"ready": None, "reason": "attempt_table_missing"}
        if "learner_attempts" in tables:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(learner_attempts)")
            }
            applied = connection.execute(
                "SELECT count(*) FROM learner_attempts WHERE profile_applied = 1"
            ).fetchone()[0]
            attempts = {
                "profile_applied": applied,
                "distinct_questions": connection.execute(
                    "SELECT count(DISTINCT question_id) FROM learner_attempts"
                ).fetchone()[0],
            }
            if "attribution_point_id" in columns:
                attempts["applied_without_precise_point"] = connection.execute(
                    "SELECT count(*) FROM learner_attempts "
                    "WHERE profile_applied = 1 AND attribution_point_id IS NULL"
                ).fetchone()[0]
            else:
                attempts["applied_without_precise_point"] = None
                attempts["reason"] = "schema_has_no_attribution_point_id"
            if "question_attributions" in tables:
                attempts["current_question_status_for_attempts"] = dict(
                    connection.execute(
                        "SELECT coalesce(q.attribution_status, 'missing'), count(*) "
                        "FROM learner_attempts AS a LEFT JOIN question_attributions AS q "
                        "ON q.question_id = a.question_id "
                        "GROUP BY coalesce(q.attribution_status, 'missing')"
                    ).fetchall()
                )

            missing = attempts["applied_without_precise_point"]
            if missing is None:
                replay = {"ready": False, "reason": "schema_has_no_attribution_point_id"}
            elif missing:
                replay = {"ready": False, "reason": "applied_attempts_missing_precise_point"}
            elif applied:
                replay = {"ready": None, "reason": "aggregate_replay_not_verified"}
            else:
                replay = {"ready": True, "reason": "no_historical_applied_attempts"}
            replay["applied_attempts"] = applied
            replay["applied_without_precise_point"] = missing

        active_questions = 0
        if "questions" in tables:
            active_questions = connection.execute(
                "SELECT count(*) FROM questions WHERE source != 'AI生成'"
            ).fetchone()[0]
        try:
            if not active_questions:
                # Match /api/ready without opening the configured business database.
                active_questions = len(json.loads(
                    Path(config.QUESTIONS_PATH).read_text(encoding="utf-8")
                ))
            validate_index(active_question_count=active_questions)
            assets_ready, assets_error = True, None
        except Exception as exc:
            assets_ready, assets_error = False, str(exc)
        schema_current = revision == SCHEMA_HEAD

        return {
            "database": str(path),
            "mode": "read_only_immutable",
            "revision": revision,
            "integrity": connection.execute("PRAGMA quick_check").fetchone()[0],
            "foreign_key_errors": len(connection.execute("PRAGMA foreign_key_check").fetchall()),
            "counts": counts,
            "historical_attempts": attempts,
            "schema_assets": {
                "ready": schema_current and assets_ready,
                "schema_current": schema_current,
                "expected_revision": SCHEMA_HEAD,
                "assets_ready": assets_ready,
                "assets_error": assets_error,
            },
            "historical_replay": replay,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", nargs="?", type=Path, default=ROOT / "data/cisp_qa.db")
    parser.add_argument("--require-schema-assets", action="store_true")
    parser.add_argument("--require-history", action="store_true")
    args = parser.parse_args()
    report = audit(args.database)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if (args.require_schema_assets and report["schema_assets"]["ready"] is not True
            or args.require_history and report["historical_replay"]["ready"] is not True):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
