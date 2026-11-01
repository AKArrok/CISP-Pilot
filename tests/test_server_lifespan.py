import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

import server
from http_auth_helpers import authenticated_http, isolated_database
from storage.models import Base


def test_lifespan_runs_warmup_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "warmup", lambda: calls.append("warmup"))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'lifespan.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with isolated_database(engine):
        with authenticated_http(server.app).client as client:
            assert client.get("/api/domains").status_code == 200
    engine.dispose()

    assert calls == ["warmup"]


def test_unmigrated_startup_keeps_database_empty(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'empty-startup.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr("data_ingest.manifest.validate_index", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("unready startup must not create tables or load models")

    monkeypatch.setattr("storage.db.create_all", forbidden)
    monkeypatch.setattr("llms.get_embeddings", forbidden)
    try:
        with isolated_database(engine):
            with TestClient(server.app) as client:
                assert client.get("/api/health").status_code == 200
                assert client.get("/api/ready").status_code == 503
                assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_quiz_import_does_not_create_database(tmp_path):
    bait = tmp_path / "quiz-import-must-not-create.sqlite"
    env = os.environ.copy()
    env.update({"DATABASE_URL": f"sqlite:///{bait.as_posix()}",
                "CISP_DB_PATH": str(tmp_path / "fallback-must-not-create.sqlite"),
                "MYSQL_PASSWORD": "", "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8"})
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import quiz"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not bait.exists()
    assert not (tmp_path / "fallback-must-not-create.sqlite").exists()


def test_python_entrypoint_uses_same_unready_lifespan(tmp_path):
    target = tmp_path / "entrypoint.sqlite"
    env = os.environ.copy()
    env.update({"DATABASE_URL": f"sqlite:///{target.as_posix()}",
                "MYSQL_PASSWORD": "", "LLM_API_KEY": "offline-test",
                "ENABLE_DENSE_RETRIEVAL": "false", "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8"})
    script = """
import runpy
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from storage import db

def run(app, **kwargs):
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/ready').status_code == 503
        assert inspect(db.get_engine()).get_table_names() == []

uvicorn.run = run
runpy.run_path('server.py', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr
