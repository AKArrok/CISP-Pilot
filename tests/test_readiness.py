from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import server
from storage import db
from storage.models import Base


ROOT = Path(__file__).resolve().parents[1]


def _engine_with_revision(tmp_path, revision=None):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'readiness.sqlite').as_posix()}"
    )
    Base.metadata.create_all(engine)
    if revision is not None:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "create table alembic_version (version_num varchar(32) not null)"
            )
            connection.exec_driver_sql(
                "insert into alembic_version (version_num) values (?)",
                (revision,),
            )
    return engine


def test_schema_head_constant_matches_alembic_graph():
    config = AlembicConfig(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_current_head() == db.SCHEMA_HEAD


def test_schema_status_reports_current_revision(tmp_path, monkeypatch):
    engine = _engine_with_revision(tmp_path, db.SCHEMA_HEAD)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "backend", lambda: "sqlite")

    status = db.schema_status()

    assert status == {
        "database_backend": "sqlite",
        "expected_revision": db.SCHEMA_HEAD,
        "current_revisions": [db.SCHEMA_HEAD],
        "migration_current": True,
    }


def test_readiness_rejects_schema_without_alembic_revision(tmp_path, monkeypatch):
    engine = _engine_with_revision(tmp_path)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "backend", lambda: "sqlite")
    monkeypatch.setattr("data_ingest.manifest.validate_index", lambda: None)

    response = TestClient(server.app).get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "migration_required"
    assert response.json()["schema"]["current_revisions"] == []


def test_readiness_accepts_current_schema_and_assets(tmp_path, monkeypatch):
    engine = _engine_with_revision(tmp_path, db.SCHEMA_HEAD)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "backend", lambda: "sqlite")
    monkeypatch.setattr("data_ingest.manifest.validate_index", lambda: None)

    response = TestClient(server.app).get("/api/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["assets"]["ready"] is True


def test_readiness_rejects_invalid_assets_after_current_migration(
    tmp_path, monkeypatch
):
    engine = _engine_with_revision(tmp_path, db.SCHEMA_HEAD)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(db, "backend", lambda: "sqlite")

    def reject_assets():
        raise RuntimeError("manifest mismatch")

    monkeypatch.setattr("data_ingest.manifest.validate_index", reject_assets)

    response = TestClient(server.app).get("/api/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "assets_invalid"
    assert response.json()["schema"]["migration_current"] is True
    assert response.json()["assets"] == {
        "ready": False,
        "error": "manifest mismatch",
    }
